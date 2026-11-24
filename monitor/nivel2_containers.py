"""
Nível 2 — containers, pela API da Docker Engine.

Falamos com o Docker pelo socket Unix ``/var/run/docker.sock``, montado no
container do monitor (ver ``docker-compose.yml``). Não usamos a CLI ``docker``
nem a biblioteca ``docker``: a API devolve JSON direto, sem parse de texto e sem
instalar nada além do httpx, que o projeto já usa.

Se o socket não estiver disponível (rodando o pytest fora do Docker, por
exemplo), ``coletar`` devolve ``disponivel=False`` com o motivo, em vez de
derrubar a thread: o painel mostra a tela com o aviso e os outros dois níveis
continuam coletando.
"""

import httpx

SOCKET = "/var/run/docker.sock"
# Host fictício: com transporte por socket Unix o nome não é resolvido, mas a
# URL precisa de um.
BASE = "http://docker"


def _cpu_pct(stats):
    """
    Percentual de CPU do container, como o ``docker stats`` calcula: quanto o
    container consumiu desde a leitura anterior (que a própria API guarda em
    ``precpu_stats``) sobre o quanto a máquina inteira consumiu no mesmo
    intervalo, multiplicado pelo número de núcleos.
    """
    cpu = stats.get("cpu_stats") or {}
    anterior = stats.get("precpu_stats") or {}
    uso = (cpu.get("cpu_usage") or {}).get("total_usage")
    uso_antes = (anterior.get("cpu_usage") or {}).get("total_usage")
    sistema = cpu.get("system_cpu_usage")
    sistema_antes = anterior.get("system_cpu_usage")
    if None in (uso, uso_antes, sistema, sistema_antes):
        return None
    delta_uso = uso - uso_antes
    delta_sistema = sistema - sistema_antes
    if delta_sistema <= 0:
        return None
    nucleos = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    return round(max(0.0, 100 * (delta_uso / delta_sistema) * nucleos), 1)


def _memoria(stats):
    """
    Uso e limite de memória. O ``usage`` do cgroup inclui o cache de arquivos,
    que não é memória "do processo": o ``docker stats`` desconta a parte
    recuperável (``inactive_file``), e fazemos o mesmo, senão o alerta dispara
    por cache.
    """
    mem = stats.get("memory_stats") or {}
    uso = mem.get("usage")
    if uso is None:
        return {"usada_bytes": None, "limite_bytes": None, "uso_pct": None}
    detalhe = mem.get("stats") or {}
    cache = detalhe.get("inactive_file", detalhe.get("total_inactive_file", 0)) or 0
    usada = max(0, uso - cache)
    limite = mem.get("limit") or None
    return {
        "usada_bytes": usada,
        "limite_bytes": limite,
        "uso_pct": round(100 * usada / limite, 1) if limite else None,
    }


class ColetorContainers:
    """
    Uma amostra do nível 2 por chamada de :meth:`coletar`.

    Para cada container são feitas duas chamadas: ``/containers/{id}/json``
    (estado, reinícios, OOMKilled) e ``/containers/{id}/stats?stream=false``
    (CPU e memória). São chamadas de I/O: a thread fica esperando a resposta e
    libera o GIL enquanto espera, então o coletor dos outros níveis continua
    rodando (ver a nota sobre o GIL no README).
    """

    def __init__(self, socket=SOCKET, timeout=5.0, rotulo_projeto=None, descobrir_projeto=True):
        self._socket = socket
        self._timeout = timeout
        # Projeto Compose a monitorar. Com um nome, filtra por ele. Sem nome e
        # com descobrir_projeto, o coletor acha sozinho o projeto do próprio
        # monitor (ver _descobrir_projeto). Sem nome e sem descoberta, mostra
        # todos os containers da máquina.
        self._rotulo_projeto = rotulo_projeto
        self._projeto_resolvido = rotulo_projeto is not None or not descobrir_projeto
        self._cliente = None

    def _conectar(self):
        if self._cliente is None:
            transporte = httpx.HTTPTransport(uds=self._socket)
            self._cliente = httpx.Client(transport=transporte, base_url=BASE, timeout=self._timeout)
        return self._cliente

    def fechar(self):
        if self._cliente is not None:
            self._cliente.close()
            self._cliente = None

    def coletar(self):
        try:
            cliente = self._conectar()
            lista = cliente.get("/containers/json", params={"all": "1"}).json()
        except (httpx.HTTPError, OSError, ValueError) as erro:
            return {
                "disponivel": False,
                "motivo": f"Docker não acessível em {self._socket}: {type(erro).__name__}",
                "containers": [],
            }

        if not self._projeto_resolvido:
            self._rotulo_projeto = self._descobrir_projeto(lista)
            self._projeto_resolvido = True

        containers = []
        for resumo in lista:
            rotulos = resumo.get("Labels") or {}
            if self._rotulo_projeto and rotulos.get("com.docker.compose.project") != self._rotulo_projeto:
                continue
            containers.append(self._detalhar(cliente, resumo))
        return {
            "disponivel": True,
            "motivo": None,
            "projeto": self._rotulo_projeto,
            "containers": containers,
        }

    @staticmethod
    def _descobrir_projeto(lista, caminho_hostname="/etc/hostname"):
        """
        Descobre a que projeto Compose o próprio monitor pertence, para a tela 2
        não misturar containers de outros projetos da máquina (numa máquina de
        desenvolvimento isso vira dezenas de linhas que não são do Origem).

        O truque: dentro de um container, o hostname é o começo do próprio id.
        Achamos esse id na lista e lemos o rótulo que o Compose colocou nele.
        Rodando fora do Docker, nada casa e devolvemos None, que não filtra.
        """
        try:
            with open(caminho_hostname, encoding="utf-8") as arquivo:
                meu_id = arquivo.read().strip()
        except OSError:
            return None
        if not meu_id:
            return None
        for resumo in lista:
            if resumo.get("Id", "").startswith(meu_id):
                return (resumo.get("Labels") or {}).get("com.docker.compose.project")
        return None

    def _detalhar(self, cliente, resumo):
        id_curto = resumo["Id"][:12]
        nome = (resumo.get("Names") or ["/?"])[0].lstrip("/")
        servico = (resumo.get("Labels") or {}).get("com.docker.compose.service", nome)
        dados = {
            "id": id_curto,
            "nome": nome,
            "servico": servico,
            "imagem": resumo.get("Image"),
            "status": resumo.get("State"),  # running, exited, restarting...
            "status_texto": resumo.get("Status"),
            "saude": None,
            "reinicios": None,
            "oom_killed": None,
            "cpu_pct": None,
            "memoria": {"usada_bytes": None, "limite_bytes": None, "uso_pct": None},
        }

        try:
            detalhe = cliente.get(f"/containers/{resumo['Id']}/json").json()
            estado = detalhe.get("State") or {}
            dados["reinicios"] = detalhe.get("RestartCount")
            dados["oom_killed"] = estado.get("OOMKilled")
            dados["saude"] = (estado.get("Health") or {}).get("Status")
            dados["iniciado_em"] = estado.get("StartedAt")
        except (httpx.HTTPError, OSError, ValueError):
            pass

        # Containers parados não têm estatísticas; a chamada ficaria pendurada.
        if dados["status"] == "running":
            try:
                stats = cliente.get(f"/containers/{resumo['Id']}/stats", params={"stream": "false"}).json()
                dados["cpu_pct"] = _cpu_pct(stats)
                dados["memoria"] = _memoria(stats)
            except (httpx.HTTPError, OSError, ValueError):
                pass
        return dados
