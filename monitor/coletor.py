"""
Coletor do monitor: produtor-consumidor com threads, nos 3 níveis do PI3.

    nível 1  produtor "sistema"     /proc/stat (por núcleo), /proc/meminfo,      ┐
                                    /proc/loadavg, /proc/vmstat, statvfs("/")    │
             produtor "processos"   /proc/<pid>/stat e status                    │
    nível 2  produtor "containers"  API do Docker: inspect + stats one-shot      ├─> BufferLimitado ─> consumidor
             produtor "docker"      fluxo /events do Docker (oom, die, start...) │   (FilaCircular +    calcula taxas,
    nível 3  produtor "servicos"    sondas HTTP / RESP / protocolo do Postgres   ┘   Lock + 2 Condition) dispara alertas
                                                                                                        e grava o estado

O consumidor é o único que escreve no estado; a API só lê. Os dois lados usam
``_lock_estado``, porque as estruturas próprias (TabelaHash, ArvoreBusca...)
não são thread-safe. Onde cada estrutura de AED entra:

| Estrutura          | Uso                                                         |
|--------------------|-------------------------------------------------------------|
| FilaCircular       | buffer do produtor-consumidor; históricos de tamanho fixo (sistema, serviço, container, latências) |
| TabelaHash         | estado por PID, por container (id) e por sonda (nome)       |
| ArvoreBusca        | eventos por (timestamp, seq), consulta por intervalo        |
| Fila               | feed dos eventos mais recentes                              |
| ListaEncadeada     | resultados de uma varredura (tamanho desconhecido)          |
| quick/merge sort   | processos por CPU/memória (quick) ou nome (merge); latências para o p95 (quick) |
| TopK               | os 5 processos que mais usam CPU                            |
| busca binária      | polling incremental: acha no histórico a última amostra que o navegador já tem |

Regra de AED: o estado não usa dict/list prontos. Cada registro é uma classe
com __slots__ (como os nós de estruturas/) e as coleções são as próprias;
dicionário só aparece na borda: JSON da API do Docker na entrada e para_json() na saída.

Alertas (com histerese: disparam ao cruzar o limite e só rearmam quando o
valor volta para baixo dele) viram eventos na ArvoreBusca:
    memória do sistema >= limite_mem      container >= limite_mem do próprio limite (mem_limit)
    processo >= limite_cpu                serviço fora do ar / latência média >= limite_latencia_ms
    OOM: evento "oom" do Docker + contador oom_kill do kernel (/proc/vmstat)
"""

import calendar
import os
import threading
import time

from concorrencia.buffer import BufferLimitado
from estruturas import (
    ArvoreBusca,
    Fila,
    FilaCircular,
    ListaEncadeada,
    TabelaHash,
    TopK,
    busca_binaria,
    merge_sort,
    quick_sort,
)
from monitor.docker_api import ROTULO_PROJETO, ROTULO_SERVICO, ClienteDocker, ErroDocker, resumo_estatisticas
from monitor.sondas import sondas_da_config

PROC = "/proc"
NUCLEOS = os.cpu_count() or 1

# Trecho da linha de comando -> serviço do docker-compose. Vale o primeiro que bater.
SERVICOS = (
    ("recomendacoes.main", "recomendacoes"),
    ("app.main", "api"),
    ("monitor.estresse", "estresse-memoria"),
    ("monitor.servidor", "monitor"),
    ("-m monitor", "monitor"),
    ("app.reconciliador", "reconciliador"),
    ("app.worker", "worker"),
    ("rq:worker", "worker"),
    ("redis-server", "redis"),
    ("postgres", "postgres"),
)

# Fora do Docker as portas publicadas no localhost; o compose troca pelos nomes dos serviços.
SONDAS_PADRAO = ("api=http://localhost:8000/,recomendacoes=http://localhost:8001/,"
                 "postgres=pg://localhost:5432,redis=redis://localhost:6379")

MAX_EVENTOS = 500
TAMANHO_FEED = 40
TAMANHO_HISTORICO = 120
JANELA_SONDAS = 60  # últimas N medidas de cada serviço (≈ 1 min com intervalo de 1 s)


def _ler(caminho):
    with open(caminho, "rb") as f:
        return f.read().decode("utf-8", "replace")


def _ler_cpus():
    """Array de (total, ocioso) em ticks: [0] = soma de todos os núcleos, [i] = núcleo i-1."""
    linhas = ListaEncadeada()
    for linha in _ler(f"{PROC}/stat").split("\n"):
        if not linha.startswith("cpu"):
            break
        campos = linha.split()[1:]
        total = 0
        for campo in campos[:8]:  # user nice system idle iowait irq softirq steal
            total += int(campo)
        linhas.inserir_fim((total, int(campos[3]) + int(campos[4])))
    return linhas.para_array()


def _ler_cpu_total():
    return _ler_cpus()[0]


def _ler_memoria():
    """(total_kb, disponivel_kb) do /proc/meminfo."""
    total = disponivel = 0
    for linha in _ler(f"{PROC}/meminfo").split("\n"):
        if linha.startswith("MemTotal:"):
            total = int(linha.split()[1])
        elif linha.startswith("MemAvailable:"):
            disponivel = int(linha.split()[1])
    return total, disponivel


def _ler_oom_kill():
    """Quantos processos o OOM Killer do kernel já matou (linha oom_kill do /proc/vmstat)."""
    try:
        for linha in _ler(f"{PROC}/vmstat").split("\n"):
            if linha.startswith("oom_kill "):
                return int(linha.split()[1])
    except OSError:
        pass
    return None


def _ler_load():
    try:
        return float(_ler(f"{PROC}/loadavg").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _ler_disco(caminho="/"):
    """(usado_kb, total_kb) do sistema de arquivos (statvfs, como o df)."""
    try:
        st = os.statvfs(caminho)
    except (AttributeError, OSError):
        return 0, 0
    total = st.f_blocks * st.f_frsize // 1024
    livre = st.f_bavail * st.f_frsize // 1024
    return total - livre, total


def _classificar(linha_comando):
    for trecho, servico in SERVICOS:
        if trecho in linha_comando:
            return servico
    return None


def _iso_para_epoch(texto):
    """'2026-11-26T14:03:12.123456789Z' -> epoch (só até os segundos)."""
    if not texto or texto.startswith("0001"):
        return None
    try:
        return calendar.timegm(time.strptime(texto[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _nome_curto(nome, rotulos):
    """'fccpd-concorrencia-fila-worker-2' -> 'worker-2' (tira o prefixo do projeto do Compose)."""
    nome = (nome or "?").lstrip("/")
    projeto = rotulos.get(ROTULO_PROJETO)
    return nome[len(projeto) + 1 :] if projeto and nome.startswith(projeto + "-") else nome


def _pct(parte, todo):
    return round(100.0 * parte / todo, 1) if todo else 0.0


class Processo:
    __slots__ = ("pid", "nome", "comando", "servico", "estado", "ticks", "rss_kb", "threads", "cpu")

    def __init__(self, pid, nome, comando, servico, estado, ticks, rss_kb, threads):
        self.pid = pid
        self.nome = nome
        self.comando = comando
        self.servico = servico
        self.estado = estado
        self.ticks = ticks  # utime + stime acumulados; o % de CPU sai da diferença entre varreduras
        self.rss_kb = rss_kb
        self.threads = threads
        self.cpu = 0.0

    def para_json(self):
        return {"pid": self.pid, "nome": self.nome, "comando": self.comando, "servico": self.servico,
                "estado": self.estado, "rss_kb": self.rss_kb, "threads": self.threads, "cpu": self.cpu}


class AmostraSistema:
    __slots__ = ("ts", "cpu", "mem", "mem_total_kb", "nucleos", "disco_usado_kb", "disco_total_kb", "load1", "oom_kill")

    def __init__(self, ts, cpu, mem, mem_total_kb, nucleos, disco_usado_kb, disco_total_kb, load1, oom_kill):
        self.ts = ts
        self.cpu = cpu
        self.mem = mem
        self.mem_total_kb = mem_total_kb
        self.nucleos = nucleos  # array: % de cada núcleo
        self.disco_usado_kb = disco_usado_kb
        self.disco_total_kb = disco_total_kb
        self.load1 = load1
        self.oom_kill = oom_kill

    def para_json(self):
        return {"ts": self.ts, "cpu": self.cpu, "mem": self.mem, "mem_total_kb": self.mem_total_kb,
                "nucleos": [n for n in self.nucleos], "disco_usado_kb": self.disco_usado_kb,
                "disco_total_kb": self.disco_total_kb, "load1": self.load1, "oom_kill": self.oom_kill}


class Evento:
    __slots__ = ("ts", "seq", "tipo", "texto")

    def __init__(self, ts, seq, tipo, texto):
        self.ts = ts
        self.seq = seq
        self.tipo = tipo
        self.texto = texto

    def para_json(self):
        return {"ts": self.ts, "seq": self.seq, "tipo": self.tipo, "texto": self.texto}


class SomaServico:
    """Acumulador de uma varredura: CPU, memória e nº de processos de um serviço."""

    __slots__ = ("cpu", "rss_kb", "processos")

    def __init__(self):
        self.cpu = 0.0
        self.rss_kb = 0
        self.processos = 0


class EstadoContainer:
    """Nível 2: um container do projeto. Taxas (CPU, rede) saem da diferença entre duas varreduras."""

    __slots__ = ("id", "nome", "servico", "status", "saude", "reinicios", "oom_killed", "codigo_saida",
                 "limite_configurado", "mem_uso", "mem_limite", "cpu", "rx_s", "tx_s", "perdidos",
                 "erros_rede", "pids", "iniciado_em", "alerta_mem", "serie_mem", "_cpu_ant", "_rede_ant")

    def __init__(self, id_, nome, servico):
        self.id = id_
        self.nome = nome
        self.servico = servico
        self.status = "?"
        self.saude = None
        self.reinicios = 0
        self.oom_killed = False
        self.codigo_saida = None
        self.limite_configurado = 0  # bytes do mem_limit; 0 = sem limite (vale a memória da VM)
        self.mem_uso = 0
        self.mem_limite = 0
        self.cpu = 0.0
        self.rx_s = 0.0
        self.tx_s = 0.0
        self.perdidos = 0
        self.erros_rede = 0
        self.pids = 0
        self.iniciado_em = None
        self.alerta_mem = False
        self.serie_mem = FilaCircular(TAMANHO_HISTORICO // 2, sobrescrever=True)  # % do limite
        self._cpu_ant = None
        self._rede_ant = None

    def mem_pct(self):
        return _pct(self.mem_uso, self.mem_limite)

    def para_json(self, agora):
        return {"id": self.id[:12], "nome": self.nome, "servico": self.servico, "status": self.status,
                "saude": self.saude, "reinicios": self.reinicios, "oom_killed": self.oom_killed,
                "codigo_saida": self.codigo_saida, "limite_configurado": self.limite_configurado,
                "mem_uso": self.mem_uso, "mem_limite": self.mem_limite, "mem_pct": self.mem_pct(),
                "cpu": self.cpu, "rx_s": round(self.rx_s), "tx_s": round(self.tx_s), "perdidos": self.perdidos,
                "erros_rede": self.erros_rede, "pids": self.pids, "alerta_mem": self.alerta_mem,
                "no_ar_s": None if self.iniciado_em is None or self.status != "running" else round(agora - self.iniciado_em),
                "serie_mem": [v for v in self.serie_mem]}


class Medida:
    __slots__ = ("ts", "ok", "ms")

    def __init__(self, ts, ok, ms):
        self.ts = ts
        self.ok = ok
        self.ms = ms


class EstadoSonda:
    """Nível 3: um serviço sondado. A janela guarda as últimas medidas para latência, jitter e perda."""

    __slots__ = ("nome", "url", "ok", "detalhe", "desde", "janela", "total_ok", "total_falhas", "alerta_latencia")

    def __init__(self, nome, url):
        self.nome = nome
        self.url = url
        self.ok = None  # None = ainda não medido
        self.detalhe = ""
        self.desde = None  # quando entrou no estado atual (no ar / fora do ar)
        self.janela = FilaCircular(JANELA_SONDAS, sobrescrever=True)  # de Medida
        self.total_ok = 0
        self.total_falhas = 0
        self.alerta_latencia = False


def estatisticas_latencia(medidas):
    """Latência média, p95, jitter e perda de um array de Medida (em ordem de tempo).

    jitter = média de |Δ| entre latências consecutivas que deram certo (a ideia
    do jitter da RFC 3550, sem o filtro exponencial). perda = falhas / total.
    """
    n = len(medidas)
    oks = ListaEncadeada()
    soma = 0.0
    soma_delta = 0.0
    deltas = 0
    anterior = None
    for m in medidas:
        if not m.ok:
            continue
        oks.inserir_fim(m.ms)
        soma += m.ms
        if anterior is not None:
            soma_delta += abs(m.ms - anterior)
            deltas += 1
        anterior = m.ms
    if not n:
        return None
    validos = oks.para_array()
    k = len(validos)
    resultado = {"amostras": n, "perda_pct": _pct(n - k, n), "media_ms": None, "p95_ms": None,
                 "min_ms": None, "max_ms": None, "jitter_ms": None}
    if k:
        ordenadas = quick_sort(validos)
        p95 = ordenadas[max(0, -(-95 * k // 100) - 1)]  # ceil(0,95·k) - 1
        resultado.update(media_ms=round(soma / k, 2), p95_ms=round(p95, 2),
                         min_ms=round(ordenadas[0], 2), max_ms=round(ordenadas[k - 1], 2),
                         jitter_ms=round(soma_delta / deltas, 2) if deltas else 0.0)
    return resultado


def _ler_processo(pid):
    """Processo lido do /proc, ou None se ele sumiu no meio da leitura."""
    try:
        stat = _ler(f"{PROC}/{pid}/stat")
        status = _ler(f"{PROC}/{pid}/status")
        comando = _ler(f"{PROC}/{pid}/cmdline").replace("\0", " ").strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # O nome (2º campo) vem entre parênteses e pode ter espaços: corta pelo último ')'.
    fecha = stat.rfind(")")
    nome = stat[stat.find("(") + 1 : fecha]
    campos = stat[fecha + 2 :].split()
    estado = campos[0]
    ticks = int(campos[11]) + int(campos[12])  # utime + stime
    rss_kb = threads = 0
    for linha in status.split("\n"):
        if linha.startswith("VmRSS:"):
            rss_kb = int(linha.split()[1])
        elif linha.startswith("Threads:"):
            threads = int(linha.split()[1])
    return Processo(pid, nome, comando[:160] or f"[{nome}]", _classificar(comando), estado, ticks, rss_kb, threads)


class Coletor:
    def __init__(self, intervalo=1.0, capacidade_buffer=8, limite_cpu=80.0, limite_mem=85.0,
                 limite_latencia_ms=500.0, sondas=None, docker=None, intervalo_containers=2.0,
                 intervalo_sondas=1.0, timeout_sonda=1.0, projeto=None):
        self.disponivel = os.path.exists(f"{PROC}/stat")
        self.intervalo = intervalo
        self.intervalo_containers = intervalo_containers
        self.intervalo_sondas = intervalo_sondas
        self.timeout_sonda = timeout_sonda
        self.limite_cpu = limite_cpu
        self.limite_mem = limite_mem
        self.limite_latencia_ms = limite_latencia_ms
        self.buffer = BufferLimitado(capacidade_buffer)
        self.docker = docker if docker is not None else ClienteDocker()
        self.sondas = sondas_da_config(SONDAS_PADRAO if sondas is None else sondas)
        self.projeto = projeto

        self._lock_estado = threading.Lock()
        self._parar = threading.Event()
        self._retomar = threading.Event()  # limpo = consumidor pausado
        self._retomar.set()
        self._threads = ListaEncadeada()

        # --- estado (só o consumidor escreve; tudo sob _lock_estado) ---
        self.processos = TabelaHash()  # pid -> Processo
        self.historico = FilaCircular(TAMANHO_HISTORICO, sobrescrever=True)  # de AmostraSistema
        self.historico_servicos = TabelaHash()  # serviço -> FilaCircular de (ts, cpu, rss_kb, processos)
        self.containers = TabelaHash()  # id -> EstadoContainer
        self.estado_sondas = TabelaHash()  # nome -> EstadoSonda
        for sonda in self.sondas:
            self.estado_sondas[sonda.nome] = EstadoSonda(sonda.nome, sonda.url)
        self.docker_situacao = "desligado"  # texto para o painel (socket ausente, erro, ok)
        self.eventos = ArvoreBusca()  # (ts, seq) -> Evento
        self.feed = Fila()  # de Evento
        self.situacao_threads = TabelaHash()  # nome da thread -> (estado, desde)
        self.contadores = TabelaHash()
        for nome in ("produzidas", "consumidas", "bloqueios", "eventos", "alertas"):
            self.contadores[nome] = 0
        self._seq = 0
        self._ultimo_sistema = None  # array de (total, ocioso) da amostra anterior
        self._ultimo_oom_kill = None
        self._alerta_mem_sistema = False
        self._ultimo_total_processos = None
        self._primeira_varredura = True

    # ------------------------------------------------------------------ threads

    def iniciar(self):
        if not self._threads.vazia():
            return
        alvos = ListaEncadeada()
        if self.disponivel:
            alvos.inserir_fim(("produtor-sistema", self._produtor, ("lendo /proc", self._amostra_sistema, lambda: self.intervalo)))
            alvos.inserir_fim(("produtor-processos", self._produtor, ("lendo /proc", self._amostra_processos, lambda: self.intervalo)))
        if self.docker.disponivel:
            alvos.inserir_fim(("produtor-containers", self._produtor, ("consultando o Docker", self._amostra_containers, lambda: self.intervalo_containers)))
            alvos.inserir_fim(("produtor-docker", self._produtor_eventos_docker, ()))
        else:
            self.docker_situacao = f"sem acesso ao socket do Docker ({self.docker.caminho})"
        if len(self.sondas):
            alvos.inserir_fim(("produtor-servicos", self._produtor, ("sondando serviços", self._amostra_sondas, lambda: self.intervalo_sondas)))
        alvos.inserir_fim(("consumidor", self._consumidor, ()))
        for nome, alvo, args in alvos:
            self._marcar(nome, "iniciando")
            t = threading.Thread(target=alvo, args=(nome, *args), name=nome, daemon=True)
            self._threads.inserir_fim(t)
            t.start()

    def parar(self):
        self._parar.set()
        self._retomar.set()
        for t in self._threads:
            if t.name != "produtor-docker":  # preso no fluxo de eventos; é daemon
                t.join(timeout=2)

    def pausar_consumidor(self, pausado: bool):
        if pausado:
            self._retomar.clear()
        else:
            self._retomar.set()
        self._evento("controle", "Consumidor pausado" if pausado else "Consumidor retomado")

    def _marcar(self, thread, estado):
        with self._lock_estado:
            self.situacao_threads[thread] = (estado, time.time())

    def _somar(self, contador, valor=1):
        with self._lock_estado:
            self.contadores[contador] = self.contadores[contador] + valor

    def _colocar(self, nome, amostra):
        """Põe no buffer; se ele estiver cheio, o produtor dorme em _nao_cheio."""
        self._marcar(nome, "colocando no buffer")
        bloqueado = False
        while not self._parar.is_set():
            try:
                self.buffer.colocar(amostra, timeout=0.25)
                self._somar("produzidas")
                return
            except TimeoutError:
                # Continua dormindo dentro do colocar(); o timeout só existe
                # para atualizar o painel e conseguir encerrar a thread.
                if not bloqueado:
                    bloqueado = True
                    self._somar("bloqueios")
                    self._marcar(nome, "bloqueado: buffer cheio")
                    self._evento("buffer", f"{nome} dormindo em _nao_cheio (buffer cheio)")

    def _produtor(self, nome, rotulo, ler_amostra, intervalo):
        while not self._parar.is_set():
            self._marcar(nome, rotulo)
            try:
                amostra = ler_amostra()
            except Exception as erro:  # uma leitura ruim não pode matar a thread
                self._marcar(nome, f"erro: {erro}"[:80])
                amostra = None
            if amostra is not None:
                self._colocar(nome, amostra)
            self._marcar(nome, "dormindo")
            self._parar.wait(intervalo())

    def _produtor_eventos_docker(self, nome):
        while not self._parar.is_set():
            try:
                projeto = self._projeto()
                self._marcar(nome, "esperando evento do Docker")
                for ev in self.docker.eventos(projeto):
                    self._colocar(nome, ("docker", time.time(), ev))
                    self._marcar(nome, "esperando evento do Docker")
            except ErroDocker as erro:
                self._marcar(nome, f"erro: {erro}"[:80])
            self._parar.wait(5)

    def _consumidor(self, nome):
        tratadores = TabelaHash()
        tratadores["sistema"] = self._consumir_sistema
        tratadores["processos"] = self._consumir_processos
        tratadores["containers"] = self._consumir_containers
        tratadores["docker"] = self._consumir_evento_docker
        tratadores["servicos"] = self._consumir_sondas
        while not self._parar.is_set():
            if not self._retomar.is_set():
                self._marcar(nome, "pausado")
                self._retomar.wait()
                continue
            self._marcar(nome, "esperando item")
            try:
                amostra = self.buffer.retirar(timeout=0.25)
            except TimeoutError:
                self._marcar(nome, "dormindo: buffer vazio")
                continue
            self._marcar(nome, f"processando {amostra[0]}")
            try:
                tratadores[amostra[0]](*amostra[1:])
            except Exception as erro:
                self._evento("erro", f"consumidor: falha ao tratar {amostra[0]}: {erro}"[:160])
            self._somar("consumidas")

    # ---------------------------------------------------------------- produção

    def _amostra_sistema(self):
        cpus = _ler_cpus()
        mem_total, mem_disp = _ler_memoria()
        disco_usado, disco_total = _ler_disco()
        return ("sistema", time.time(), cpus, mem_total, mem_disp, disco_usado, disco_total, _ler_load(), _ler_oom_kill())

    def _amostra_processos(self):
        total, _ = _ler_cpu_total()
        lista = ListaEncadeada()
        for entrada in os.listdir(PROC):
            if entrada.isdigit():
                processo = _ler_processo(int(entrada))
                if processo is not None:
                    lista.inserir_fim(processo)
        return ("processos", time.time(), total, lista.para_array())

    def _projeto(self):
        if self.projeto is None:
            self.projeto = os.getenv("MONITOR_PROJETO") or self.docker.projeto_proprio() or ""
        return self.projeto or None

    def _amostra_containers(self):
        """Lista os containers do projeto e lê inspect (+ stats dos que estão rodando)."""
        try:
            resumo = self.docker.listar(self._projeto())
            lista = ListaEncadeada()
            for c in resumo:
                info = self.docker.inspecionar(c["Id"])
                stats = resumo_estatisticas(self.docker.estatisticas(c["Id"])) if c.get("State") == "running" else None
                lista.inserir_fim((info, stats))
        except ErroDocker as erro:
            return ("containers", time.time(), None, str(erro))
        return ("containers", time.time(), lista.para_array(), None)

    def _amostra_sondas(self):
        """Mede todos os serviços em paralelo, com prazo; sonda que não volta a tempo conta como perda."""
        prazo = self.timeout_sonda + 0.5
        n = len(self.sondas)
        caixas = [None] * n
        threads = [None] * n
        for i in range(n):
            sonda = self.sondas[i]

            def medir(i=i, sonda=sonda):
                caixas[i] = sonda.medir(self.timeout_sonda)

            threads[i] = threading.Thread(target=medir, name=f"sonda-{sonda.nome}", daemon=True)
            threads[i].start()
        limite = time.perf_counter() + prazo
        resultados = [None] * n
        for i in range(n):
            threads[i].join(max(0.0, limite - time.perf_counter()))
            resultado = caixas[i]
            if resultado is None:  # ainda preso (ex.: DNS de container parado); a thread termina sozinha
                resultado = (False, prazo * 1000, f"sem resposta em {prazo:.1f} s")
            resultados[i] = (self.sondas[i].nome, *resultado)
        return ("servicos", time.time(), resultados)

    # ---------------------------------------------------------------- consumo

    def _consumir_sistema(self, ts, cpus, mem_total, mem_disp, disco_usado, disco_total, load1, oom_kill):
        anterior = self._ultimo_sistema
        self._ultimo_sistema = cpus
        if anterior is None or cpus[0][0] == anterior[0][0] or len(cpus) != len(anterior):
            self._ultimo_oom_kill = oom_kill
            return
        uso = [0.0] * len(cpus)
        for i in range(len(cpus)):
            d_total = cpus[i][0] - anterior[i][0]
            d_ocioso = cpus[i][1] - anterior[i][1]
            uso[i] = round(100.0 * (1 - d_ocioso / d_total), 1) if d_total else 0.0
        nucleos = [0.0] * (len(cpus) - 1)
        for i in range(1, len(cpus)):
            nucleos[i - 1] = uso[i]
        mem = 100.0 * (1 - mem_disp / mem_total) if mem_total else 0.0
        with self._lock_estado:
            self.historico.enfileirar(AmostraSistema(ts, uso[0], round(mem, 1), mem_total, nucleos,
                                                     disco_usado, disco_total, load1, oom_kill))

        if mem >= self.limite_mem and not self._alerta_mem_sistema:
            self._alerta_mem_sistema = True
            self._alerta("memoria", f"Memória do sistema em {mem:.1f}% (limite {self.limite_mem:.0f}%): "
                                    f"{(mem_total - mem_disp) // 1024} MB de {mem_total // 1024} MB")
        elif mem < self.limite_mem - 5 and self._alerta_mem_sistema:
            self._alerta_mem_sistema = False
            self._evento("normal", f"Memória do sistema voltou para {mem:.1f}%")
        if oom_kill is not None and self._ultimo_oom_kill is not None and oom_kill > self._ultimo_oom_kill:
            self._alerta("oom", f"Kernel: o OOM Killer matou {oom_kill - self._ultimo_oom_kill} processo(s) "
                                f"(/proc/vmstat oom_kill = {oom_kill})")
        self._ultimo_oom_kill = oom_kill

    def _consumir_processos(self, ts, total, processos):
        delta_total = None if self._ultimo_total_processos is None else total - self._ultimo_total_processos
        self._ultimo_total_processos = total
        novos = TabelaHash(capacidade_inicial=max(16, len(processos) * 2))
        eventos = ListaEncadeada()
        por_servico = TabelaHash()

        with self._lock_estado:
            for p in processos:
                antigo = self.processos.obter(p.pid, None)
                if antigo is not None and delta_total:
                    # % de um núcleo, como no top: 100% = um núcleo inteiro ocupado.
                    p.cpu = round(100.0 * NUCLEOS * (p.ticks - antigo.ticks) / delta_total, 1)
                    if p.cpu >= self.limite_cpu > antigo.cpu:
                        eventos.inserir_fim(("alerta", f"{p.nome} (PID {p.pid}) passou de {self.limite_cpu:.0f}% de CPU: {p.cpu}%"))
                elif antigo is None and not self._primeira_varredura and p.servico:
                    eventos.inserir_fim(("inicio", f"{p.servico}: processo {p.nome} iniciou (PID {p.pid})"))
                novos[p.pid] = p
                if p.servico:
                    soma = por_servico.obter(p.servico, None)
                    if soma is None:
                        soma = SomaServico()
                        por_servico[p.servico] = soma
                    soma.cpu += p.cpu
                    soma.rss_kb += p.rss_kb
                    soma.processos += 1

            if not self._primeira_varredura:
                for pid, antigo in self.processos.itens():
                    if pid not in novos and antigo.servico:
                        eventos.inserir_fim(("fim", f"{antigo.servico}: processo {antigo.nome} terminou (PID {pid})"))

            self.processos = novos
            for servico, soma in por_servico.itens():
                janela = self.historico_servicos.obter(servico, None)
                if janela is None:
                    janela = FilaCircular(TAMANHO_HISTORICO, sobrescrever=True)
                    self.historico_servicos[servico] = janela
                janela.enfileirar((ts, round(soma.cpu, 1), soma.rss_kb, soma.processos))
            # Serviço sem nenhum processo vivo (container parado) sai do painel.
            sumiram = ListaEncadeada()
            for servico in self.historico_servicos.chaves():
                if servico not in por_servico:
                    sumiram.inserir_fim(servico)
            for servico in sumiram:
                self.historico_servicos.remover(servico)
            self._primeira_varredura = False

        for tipo, texto in eventos:
            if tipo == "alerta":
                self._alerta(tipo, texto)
            else:
                self._evento(tipo, texto)

    def _consumir_containers(self, ts, registros, erro):
        if registros is None:
            with self._lock_estado:
                self.docker_situacao = f"erro: {erro}"[:160]
            return
        alertas = ListaEncadeada()
        vistos = TabelaHash()
        with self._lock_estado:
            self.docker_situacao = "ok"
            for info, stats in registros:
                id_ = info["Id"]
                vistos[id_] = True
                estado = info.get("State") or {}
                rotulos = (info.get("Config") or {}).get("Labels") or {}
                c = self.containers.obter(id_, None)
                if c is None:
                    c = EstadoContainer(id_, _nome_curto(info.get("Name"), rotulos), rotulos.get(ROTULO_SERVICO, "?"))
                    self.containers[id_] = c
                c.status = estado.get("Status", "?")
                c.saude = (estado.get("Health") or {}).get("Status")
                c.reinicios = info.get("RestartCount", 0)
                c.oom_killed = bool(estado.get("OOMKilled"))
                c.codigo_saida = estado.get("ExitCode")
                c.iniciado_em = _iso_para_epoch(estado.get("StartedAt"))
                c.limite_configurado = (info.get("HostConfig") or {}).get("Memory") or 0
                if stats is None:  # parado: zera as taxas; o alerta de memória deixa de valer
                    c.cpu = c.rx_s = c.tx_s = 0.0
                    c.mem_uso = c.pids = 0
                    c._cpu_ant = c._rede_ant = None
                    c.alerta_mem = False
                    continue
                if c._cpu_ant is not None:
                    d_cont = stats["cpu_total"] - c._cpu_ant[0]
                    d_sist = stats["cpu_sistema"] - c._cpu_ant[1]
                    # Mesmo cálculo do `docker stats`: 100% = um núcleo inteiro.
                    c.cpu = round(100.0 * stats["cpus"] * d_cont / d_sist, 1) if d_sist > 0 and d_cont >= 0 else 0.0
                c._cpu_ant = (stats["cpu_total"], stats["cpu_sistema"])
                if c._rede_ant is not None and ts > c._rede_ant[0]:
                    dt = ts - c._rede_ant[0]
                    c.rx_s = max(0.0, (stats["rx"] - c._rede_ant[1]) / dt)
                    c.tx_s = max(0.0, (stats["tx"] - c._rede_ant[2]) / dt)
                c._rede_ant = (ts, stats["rx"], stats["tx"])
                c.mem_uso = stats["mem_uso"]
                c.mem_limite = stats["mem_limite"]
                c.perdidos = stats["perdidos"]
                c.erros_rede = stats["erros_rede"]
                c.pids = stats["pids"]
                pct = c.mem_pct()
                c.serie_mem.enfileirar(pct)
                if pct >= self.limite_mem and not c.alerta_mem:
                    c.alerta_mem = True
                    de_quem = "do mem_limit" if c.limite_configurado else "da memória da VM (sem mem_limit)"
                    alertas.inserir_fim(("memoria", f"{c.nome}: memória em {pct:.1f}% {de_quem} "
                                                    f"({c.mem_uso // 1048576} de {c.mem_limite // 1048576} MB)"))
                elif pct < self.limite_mem - 5 and c.alerta_mem:
                    c.alerta_mem = False
                    alertas.inserir_fim(("normal", f"{c.nome}: memória voltou para {pct:.1f}% do limite"))
            # Container removido (docker compose rm / down) sai do painel.
            removidos = ListaEncadeada()
            for id_ in self.containers.chaves():
                if id_ not in vistos:
                    removidos.inserir_fim(id_)
            for id_ in removidos:
                self.containers.remover(id_)
        for tipo, texto in alertas:
            if tipo == "normal":
                self._evento(tipo, texto)
            else:
                self._alerta(tipo, texto)

    def _consumir_evento_docker(self, ts, ev):
        acao = ev.get("Action", "")
        atributos = (ev.get("Actor") or {}).get("Attributes") or {}
        nome = _nome_curto(atributos.get("name"), atributos)  # os rótulos vêm junto nos atributos
        if acao == "oom":
            self._alerta("oom", f"{nome}: OOM Killer matou o processo (estourou o mem_limit)")
        elif acao == "die":
            codigo = atributos.get("exitCode", "?")
            sinal = " = SIGKILL (OOM ou kill -9)" if codigo == "137" else " = SIGTERM" if codigo == "143" else ""
            self._evento("fim", f"{nome}: container parou (código {codigo}{sinal})")
        elif acao == "start":
            self._evento("inicio", f"{nome}: container iniciou")
        elif acao == "restart":
            self._evento("inicio", f"{nome}: container reiniciado")
        elif acao.startswith("health_status"):
            situacao = acao.split(":", 1)[-1].strip()
            if situacao == "healthy":
                self._evento("normal", f"{nome}: health check do Docker = healthy")
            else:
                self._alerta("servico", f"{nome}: health check do Docker = {situacao}")
        elif acao in ("kill", "stop"):
            self._evento("controle", f"{nome}: {acao} (sinal {atributos.get('signal', '-')})")

    def _consumir_sondas(self, ts, resultados):
        eventos = ListaEncadeada()
        with self._lock_estado:
            for nome, ok, ms, detalhe in resultados:
                s = self.estado_sondas[nome]
                s.janela.enfileirar(Medida(ts, ok, round(ms, 2)))
                if ok:
                    s.total_ok += 1
                else:
                    s.total_falhas += 1
                if s.ok is None:
                    s.desde = ts
                    if not ok:
                        eventos.inserir_fim(("servico", f"{nome}: fora do ar ({detalhe})"))
                elif ok != s.ok:
                    duracao = ts - s.desde
                    if ok:
                        eventos.inserir_fim(("normal", f"{nome}: voltou depois de {duracao:.0f} s fora do ar"))
                    else:
                        eventos.inserir_fim(("servico", f"{nome}: caiu depois de {duracao:.0f} s no ar ({detalhe})"))
                    s.desde = ts
                s.ok = ok
                s.detalhe = detalhe
                stats = estatisticas_latencia(self._array(s.janela))
                media = stats["media_ms"] if stats else None
                if media is not None and media >= self.limite_latencia_ms and not s.alerta_latencia:
                    s.alerta_latencia = True
                    eventos.inserir_fim(("latencia", f"{nome}: latência média {media:.0f} ms (limite {self.limite_latencia_ms:.0f} ms)"))
                elif s.alerta_latencia and (media is None or media < self.limite_latencia_ms * 0.8):
                    s.alerta_latencia = False
                    eventos.inserir_fim(("normal", f"{nome}: latência normalizada"))
        for tipo, texto in eventos:
            if tipo == "normal":
                self._evento(tipo, texto)
            else:
                self._alerta(tipo, texto)

    def _alerta(self, tipo, texto):
        self._somar("alertas")
        self._evento(tipo, texto)

    def _evento(self, tipo, texto):
        with self._lock_estado:
            ts = time.time()
            self._seq += 1
            evento = Evento(ts, self._seq, tipo, texto)
            self.eventos.inserir((ts, self._seq), evento)
            if len(self.eventos) > MAX_EVENTOS:
                self.eventos.remover(self.eventos.minimo())
            self.feed.enfileirar(evento)
            if len(self.feed) > TAMANHO_FEED:
                self.feed.desenfileirar()
            self.contadores["eventos"] = self.contadores["eventos"] + 1

    # ---------------------------------------------------------------- leitura (API)

    def instantaneo(self, desde=None, ordem="cpu", limite=30, janela_eventos=300.0):
        """Tudo que o painel precisa numa resposta. ``desde`` = ts da última amostra que o cliente tem."""
        posicoes, inicio_buffer, ocupacao = self.buffer.retrato()
        agora = time.time()
        with self._lock_estado:
            historico = self._array(self.historico)
            novas = historico
            if desde is not None:
                # O cliente manda o ts exato de uma amostra que recebeu antes, então
                # a busca é por igualdade. -1 = saiu da janela: manda tudo de novo.
                i = busca_binaria(historico, desde, chave=lambda a: a.ts)
                if i >= 0:
                    novas = self._fatia(historico, i + 1)

            processos = self._array_valores(self.processos)
            top = TopK(5)
            for p in processos:
                top.oferecer(p, p.cpu)

            # (nome, pontos) ordenados por nome; o último ponto é a varredura atual.
            servicos = merge_sort(
                ((servico, self._array(janela)) for servico, janela in self.historico_servicos.itens()),
                chave=lambda par: par[0],
            )
            containers = merge_sort(self._array_valores(self.containers), chave=lambda c: (c.servico, c.nome))
            sondas = merge_sort(self._array_valores(self.estado_sondas), chave=lambda s: s.nome)

            eventos = ListaEncadeada()
            for _, ev in self.eventos.intervalo((agora - janela_eventos, 0), (agora, float("inf"))):
                eventos.inserir_fim(ev)

            resposta = {
                "disponivel": self.disponivel,
                "agora": agora,
                "nucleos": NUCLEOS,
                "intervalo": self.intervalo,
                "limites": {"cpu": self.limite_cpu, "mem": self.limite_mem, "latencia_ms": self.limite_latencia_ms},
                "limite_cpu": self.limite_cpu,
                "pausado": not self._retomar.is_set(),
                "historico": [a.para_json() for a in novas],
                "historico_completo": desde is None or len(novas) == len(historico),
                "top": [{"pid": p.pid, "nome": p.nome, "cpu": s} for p, s in top.resultado()],
                "servicos": [{"servico": nome, "cpu": pontos[-1][1], "rss_kb": pontos[-1][2], "processos": pontos[-1][3],
                              "serie": [c for _, c, _, _ in pontos]} for nome, pontos in servicos],
                "docker": {"situacao": self.docker_situacao, "projeto": self.projeto or None},
                "containers": [c.para_json(agora) for c in containers],
                "sondas": [self._sonda_json(s, agora) for s in sondas],
                "eventos": [ev.para_json() for ev in eventos],
                "feed": [ev.para_json() for ev in self.feed],
                "threads": [{"nome": n, "estado": e, "desde": round(agora - d, 1)}
                            for n, (e, d) in self._ordenar_itens(self.situacao_threads)],
                "contadores": {n: v for n, v in self.contadores.itens()},
                "alertas_ativos": self._alertas_ativos(containers, sondas),
                "estruturas": {
                    "processos_tabela": {"itens": len(self.processos), "baldes": self.processos.capacidade},
                    "eventos_arvore": {"itens": len(self.eventos), "altura": self.eventos.altura()},
                    "historico_fila": {"itens": len(self.historico), "capacidade": self.historico.capacidade},
                    "feed_fila": {"itens": len(self.feed)},
                    "containers_tabela": {"itens": len(self.containers), "baldes": self.containers.capacidade},
                },
            }

        ordenados = self._ordenar_processos(processos, ordem)
        resposta["total_processos"] = len(ordenados)
        resposta["processos"] = [p.para_json() for p in self._fatia(ordenados, 0, limite)]
        resposta["buffer"] = {
            "capacidade": len(posicoes),
            "inicio": inicio_buffer,
            "ocupacao": ocupacao,
            "posicoes": [None if a is None else {"tipo": a[0], "ts": a[1]} for a in posicoes],
        }
        return resposta

    def _sonda_json(self, s, agora):
        medidas = self._array(s.janela)
        return {"nome": s.nome, "url": s.url, "ok": s.ok, "detalhe": s.detalhe,
                "estado_ha_s": None if s.desde is None else round(agora - s.desde),
                "total_ok": s.total_ok, "total_falhas": s.total_falhas,
                "disponibilidade_pct": _pct(s.total_ok, s.total_ok + s.total_falhas),
                "alerta_latencia": s.alerta_latencia,
                "janela": estatisticas_latencia(medidas),
                "serie": [m.ms if m.ok else None for m in medidas]}

    def _alertas_ativos(self, containers, sondas):
        ativos = ListaEncadeada()
        if self._alerta_mem_sistema:
            ativos.inserir_fim(f"memória do sistema acima de {self.limite_mem:.0f}%")
        for c in containers:
            if c.alerta_mem:
                ativos.inserir_fim(f"{c.nome}: memória acima de {self.limite_mem:.0f}% do limite")
            if c.status not in ("running", "created"):
                ativos.inserir_fim(f"{c.nome}: {c.status}" + (" (OOMKilled)" if c.oom_killed else ""))
        for s in sondas:
            if s.ok is False:
                ativos.inserir_fim(f"{s.nome}: fora do ar")
            if s.alerta_latencia:
                ativos.inserir_fim(f"{s.nome}: latência alta")
        return [a for a in ativos]

    @staticmethod
    def _ordenar_processos(processos, ordem):
        if ordem == "nome":
            # Merge sort é estável: empates no nome continuam na ordem de PID.
            por_pid = quick_sort(processos, chave=lambda p: p.pid)
            return merge_sort(por_pid, chave=lambda p: p.nome.lower())
        if ordem == "pid":
            return quick_sort(processos, chave=lambda p: p.pid)
        if ordem == "mem":
            return quick_sort(processos, chave=lambda p: (p.rss_kb, p.cpu), decrescente=True)
        return quick_sort(processos, chave=lambda p: (p.cpu, p.rss_kb), decrescente=True)

    @staticmethod
    def _array(iteravel):
        lista = ListaEncadeada()
        for item in iteravel:
            lista.inserir_fim(item)
        return lista.para_array()

    def _array_valores(self, tabela):
        lista = ListaEncadeada()
        for _, valor in tabela.itens():
            lista.inserir_fim(valor)
        return lista.para_array()

    def _ordenar_itens(self, tabela):
        return merge_sort(tabela.itens(), chave=lambda par: par[0])

    @staticmethod
    def _fatia(array, inicio, fim=None):
        fim = len(array) if fim is None else min(fim, len(array))
        resultado = [None] * max(0, fim - inicio)
        for i in range(inicio, fim):
            resultado[i - inicio] = array[i]
        return resultado
