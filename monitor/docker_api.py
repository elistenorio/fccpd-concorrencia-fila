"""
Cliente mínimo da API do Docker Engine pelo socket Unix (/var/run/docker.sock).

Só biblioteca padrão: a API do Docker é HTTP comum, só que num socket Unix em vez
de uma porta TCP. O socket é montado SOMENTE LEITURA no container do monitor
(docker-compose.yml) e o monitor só faz GET: lista, inspeciona, lê estatísticas
e escuta o fluxo de eventos.

Rotas usadas (https://docs.docker.com/reference/api/engine/):
    GET /containers/json?all=1&filters=...         containers do projeto
    GET /containers/{id}/json                      status, reinícios, OOMKilled, limite
    GET /containers/{id}/stats?stream=false&one-shot=true   CPU, memória, rede (sem esperar 1 s)
    GET /events?filters=...                        fluxo contínuo: oom, die, start, restart...
"""

import http.client
import json
import os
import socket
from urllib.parse import quote

SOCKET_PADRAO = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
ROTULO_PROJETO = "com.docker.compose.project"
ROTULO_SERVICO = "com.docker.compose.service"


class _ConexaoUnix(http.client.HTTPConnection):
    def __init__(self, caminho, timeout):
        super().__init__("localhost", timeout=timeout)
        self._caminho = caminho

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._caminho)
        self.sock = s


class ErroDocker(Exception):
    pass


class ClienteDocker:
    def __init__(self, caminho=SOCKET_PADRAO, timeout=3.0):
        self.caminho = caminho
        self.timeout = timeout

    @property
    def disponivel(self):
        return hasattr(socket, "AF_UNIX") and os.path.exists(self.caminho)

    def _get(self, rota, timeout=None):
        conexao = _ConexaoUnix(self.caminho, timeout or self.timeout)
        try:
            conexao.request("GET", rota)
            resposta = conexao.getresponse()
            corpo = resposta.read()
            if resposta.status >= 400:
                raise ErroDocker(f"{rota}: HTTP {resposta.status} {corpo[:200]!r}")
            return json.loads(corpo)
        except OSError as erro:
            raise ErroDocker(f"{rota}: {erro}") from erro
        finally:
            conexao.close()

    # ------------------------------------------------------------------ rotas

    def projeto_proprio(self):
        """Projeto do Compose do container onde o monitor roda (hostname = id curto)."""
        try:
            info = self._get(f"/containers/{socket.gethostname()}/json")
        except ErroDocker:
            return None
        return (info.get("Config", {}).get("Labels") or {}).get(ROTULO_PROJETO)

    def listar(self, projeto=None):
        filtros = {"label": [f"{ROTULO_PROJETO}={projeto}"]} if projeto else {"label": [ROTULO_PROJETO]}
        return self._get(f"/containers/json?all=1&filters={quote(json.dumps(filtros))}")

    def inspecionar(self, id_):
        return self._get(f"/containers/{id_}/json")

    def estatisticas(self, id_):
        # one-shot: devolve uma leitura só, sem esperar 1 s pela segunda (o
        # % de CPU sai da diferença entre duas varreduras nossas).
        return self._get(f"/containers/{id_}/stats?stream=false&one-shot=true")

    def eventos(self, projeto=None):
        """Gerador infinito de eventos de container (bloqueia esperando o próximo)."""
        filtros = {"type": ["container"],
                   "event": ["oom", "die", "start", "restart", "kill", "stop", "health_status"]}
        if projeto:
            filtros["label"] = [f"{ROTULO_PROJETO}={projeto}"]
        conexao = _ConexaoUnix(self.caminho, None)  # sem timeout: o fluxo fica aberto
        try:
            conexao.request("GET", f"/events?filters={quote(json.dumps(filtros))}")
            resposta = conexao.getresponse()
            if resposta.status >= 400:
                raise ErroDocker(f"/events: HTTP {resposta.status}")
            while True:
                linha = resposta.readline()
                if not linha:
                    return
                linha = linha.strip()
                if linha:
                    yield json.loads(linha)
        except OSError as erro:
            raise ErroDocker(f"/events: {erro}") from erro
        finally:
            conexao.close()


# ---------------------------------------------------------------- conversões

def resumo_estatisticas(stats):
    """Números crus de /stats que o coletor usa (o % de CPU é calculado lá, por diferença)."""
    cpu = stats.get("cpu_stats") or {}
    mem = stats.get("memory_stats") or {}
    detalhes = mem.get("stats") or {}
    uso = mem.get("usage") or 0
    # Igual ao `docker stats`: desconta o cache de arquivos inativo (cgroup v2: inactive_file).
    cache = detalhes.get("inactive_file", detalhes.get("total_inactive_file", 0))
    rx = tx = rx_perdidos = tx_perdidos = erros = 0
    for rede in (stats.get("networks") or {}).values():
        rx += rede.get("rx_bytes", 0)
        tx += rede.get("tx_bytes", 0)
        rx_perdidos += rede.get("rx_dropped", 0)
        tx_perdidos += rede.get("tx_dropped", 0)
        erros += rede.get("rx_errors", 0) + rede.get("tx_errors", 0)
    return {
        "cpu_total": (cpu.get("cpu_usage") or {}).get("total_usage", 0),
        "cpu_sistema": cpu.get("system_cpu_usage", 0),
        "cpus": cpu.get("online_cpus") or 1,
        "mem_uso": max(0, uso - cache),
        "mem_limite": mem.get("limit") or 0,
        "rx": rx, "tx": tx, "perdidos": rx_perdidos + tx_perdidos, "erros_rede": erros,
        "pids": (stats.get("pids_stats") or {}).get("current", 0),
    }
