"""Níveis 2 e 3 do monitor e alertas de memória, sem Docker e sem /proc.

O Docker é trocado por um cliente falso; as sondas falam com servidores locais
de verdade (HTTP e um "Redis" que responde +PONG).
"""

import http.server
import socket
import threading

from monitor.coletor import Coletor, Medida, estatisticas_latencia
from monitor.docker_api import resumo_estatisticas
from monitor.sondas import Sonda, sondas_da_config


class DockerFalso:
    disponivel = False
    caminho = "(teste)"


def novo_coletor(**kw):
    kw.setdefault("sondas", "")
    return Coletor(docker=DockerFalso(), **kw)


def eventos(coletor):
    return [(e["tipo"], e["texto"]) for e in coletor.instantaneo(janela_eventos=1e9)["eventos"]]


# ------------------------------------------------------------------ latência

def test_estatisticas_latencia_media_p95_jitter_perda():
    ms = [10, 12, 11, None, 30, 10]  # None = falha
    medidas = [Medida(i, v is not None, v or 1000) for i, v in enumerate(ms)]
    r = estatisticas_latencia(medidas)
    assert r["amostras"] == 6
    assert r["perda_pct"] == round(100 / 6, 1)
    assert r["media_ms"] == round((10 + 12 + 11 + 30 + 10) / 5, 2)
    assert r["p95_ms"] == 30 and r["min_ms"] == 10 and r["max_ms"] == 30
    # |12-10| + |11-12| + |30-11| + |10-30| = 2 + 1 + 19 + 20 (a falha é pulada)
    assert r["jitter_ms"] == round(42 / 4, 2)


def test_estatisticas_latencia_tudo_falhou():
    r = estatisticas_latencia([Medida(0, False, 1500), Medida(1, False, 1500)])
    assert r["perda_pct"] == 100.0 and r["media_ms"] is None
    assert estatisticas_latencia([]) is None


# ------------------------------------------------------------------ nível 2

def info(id_, nome, status="running", oom=False, restarts=0, limite=128 * 2**20, codigo=0):
    return {"Id": id_, "Name": f"/{nome}", "RestartCount": restarts,
            "State": {"Status": status, "OOMKilled": oom, "ExitCode": codigo, "StartedAt": "2026-11-26T12:00:00.5Z"},
            "Config": {"Labels": {"com.docker.compose.service": nome.split("-")[0]}},
            "HostConfig": {"Memory": limite}}


def stats(cpu_total, cpu_sistema, mem, limite=128 * 2**20, rx=0, tx=0):
    return resumo_estatisticas({
        "cpu_stats": {"cpu_usage": {"total_usage": cpu_total}, "system_cpu_usage": cpu_sistema, "online_cpus": 4},
        "memory_stats": {"usage": mem + 10 * 2**20, "limit": limite, "stats": {"inactive_file": 10 * 2**20}},
        "networks": {"eth0": {"rx_bytes": rx, "tx_bytes": tx, "rx_dropped": 1, "tx_dropped": 0, "rx_errors": 0, "tx_errors": 0}},
        "pids_stats": {"current": 3},
    })


def test_resumo_estatisticas_desconta_cache_como_docker_stats():
    s = stats(0, 0, 50 * 2**20)
    assert s["mem_uso"] == 50 * 2**20 and s["mem_limite"] == 128 * 2**20 and s["perdidos"] == 1


def test_containers_taxas_e_alerta_de_memoria_com_histerese():
    c = novo_coletor(limite_mem=85)
    c._consumir_containers(100.0, [(info("a" * 64, "api-1"), stats(1_000, 10_000, 50 * 2**20, rx=0))], None)
    # +2000 ns de CPU do container em +8000 do sistema, 4 CPUs -> 100% (um núcleo)
    c._consumir_containers(102.0, [(info("a" * 64, "api-1"), stats(3_000, 18_000, 112 * 2**20, rx=4096))], None)
    r = c.instantaneo()["containers"][0]
    assert r["cpu"] == 100.0 and r["rx_s"] == 2048 and r["mem_pct"] == 87.5 and r["alerta_mem"]
    assert any(t == "memoria" and "87.5%" in x for t, x in eventos(c))
    assert "api-1: memória acima de 85% do limite" in c.instantaneo()["alertas_ativos"]

    # continua alto: não repete o alerta; cai abaixo de 80%: rearma e registra a volta
    c._consumir_containers(104.0, [(info("a" * 64, "api-1"), stats(3_000, 26_000, 110 * 2**20))], None)
    assert sum(1 for t, _ in eventos(c) if t == "memoria") == 1
    c._consumir_containers(106.0, [(info("a" * 64, "api-1"), stats(3_000, 34_000, 60 * 2**20))], None)
    assert not c.instantaneo()["containers"][0]["alerta_mem"]
    assert any(t == "normal" for t, _ in eventos(c))
    assert c.instantaneo()["contadores"]["alertas"] == 1


def test_container_parado_por_oom_e_removido():
    c = novo_coletor()
    # rodando acima do limite de alerta e depois morto pelo OOM: o alerta de memória sai, fica o OOMKilled
    c._consumir_containers(0.5, [(info("b" * 64, "estresse-1"), stats(0, 0, 120 * 2**20))], None)
    assert c.instantaneo()["containers"][0]["alerta_mem"]
    c._consumir_containers(1.0, [(info("b" * 64, "estresse-1", status="exited", oom=True, codigo=137), None)], None)
    r = c.instantaneo()
    assert r["containers"][0]["oom_killed"] and r["containers"][0]["codigo_saida"] == 137
    assert r["containers"][0]["no_ar_s"] is None
    assert r["alertas_ativos"] == ["estresse-1: exited (OOMKilled)"]
    c._consumir_containers(2.0, [], None)  # docker compose rm
    assert c.instantaneo()["containers"] == []
    c._consumir_containers(3.0, None, "socket fechado")
    assert c.instantaneo()["docker"]["situacao"].startswith("erro")


def test_eventos_do_docker_viram_linha_do_tempo():
    c = novo_coletor()
    ator = {"Attributes": {"name": "origem-estresse-1", "exitCode": "137", "com.docker.compose.project": "origem"}}
    c._consumir_evento_docker(1.0, {"Action": "oom", "Actor": ator})
    c._consumir_evento_docker(1.0, {"Action": "die", "Actor": ator})
    c._consumir_evento_docker(1.0, {"Action": "health_status: unhealthy", "Actor": ator})
    tipos = [t for t, _ in eventos(c)]
    assert tipos == ["oom", "fim", "servico"]
    assert eventos(c)[1][1].startswith("estresse-1: container parou (código 137 = SIGKILL")
    assert c.instantaneo()["contadores"]["alertas"] == 2  # oom + unhealthy


def test_memoria_do_sistema_e_oom_kill_do_kernel():
    c = novo_coletor(limite_mem=85)
    cpus = lambda t: [(t, t // 2), (t // 2, t // 4), (t // 2, t // 4)]
    c._consumir_sistema(1.0, cpus(1000), 1000, 500, 10, 100, 0.5, 3)
    c._consumir_sistema(2.0, cpus(2000), 1000, 100, 10, 100, 0.5, 4)  # 90% de memória; oom_kill 3 -> 4
    h = c.instantaneo()["historico"][-1]
    assert h["mem"] == 90.0 and h["nucleos"] == [50.0, 50.0] and h["oom_kill"] == 4
    tipos = [t for t, _ in eventos(c)]
    assert "memoria" in tipos and "oom" in tipos


# ------------------------------------------------------------------ nível 3

def test_sondas_queda_e_volta_viram_eventos():
    c = novo_coletor(sondas="api=http://api:8000/")
    c._consumir_sondas(10.0, [("api", True, 5.0, "HTTP 200")])
    c._consumir_sondas(11.0, [("api", False, 1500.0, "sem resposta em 1.5 s")])
    r = c.instantaneo()["sondas"][0]
    assert r["ok"] is False and r["janela"]["perda_pct"] == 50.0 and r["serie"] == [5.0, None]
    assert "api: fora do ar" in c.instantaneo()["alertas_ativos"]
    c._consumir_sondas(14.0, [("api", True, 6.0, "HTTP 200")])
    textos = [x for _, x in eventos(c)]
    assert any("caiu depois de 1 s no ar" in x for x in textos)
    assert any("voltou depois de 3 s fora do ar" in x for x in textos)
    assert c.instantaneo()["sondas"][0]["disponibilidade_pct"] == round(200 / 3, 1)


def test_alerta_de_latencia():
    c = novo_coletor(sondas="api=http://api:8000/", limite_latencia_ms=100)
    for i in range(3):
        c._consumir_sondas(float(i), [("api", True, 300.0, "HTTP 200")])
    assert [t for t, _ in eventos(c)] == ["latencia"]


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/" else 503)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_sonda_http_e_redis_contra_servidores_locais():
    http_srv = http.server.HTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()

    redis_srv = socket.socket()
    redis_srv.bind(("127.0.0.1", 0))
    redis_srv.listen()

    def responder():
        con, _ = redis_srv.accept()
        if con.recv(16) == b"PING\r\n":
            con.sendall(b"+PONG\r\n")
        con.close()

    threading.Thread(target=responder, daemon=True).start()
    try:
        porta_http = http_srv.server_address[1]
        ok, ms, detalhe = Sonda("api", f"http://127.0.0.1:{porta_http}/").medir()
        assert ok and detalhe == "HTTP 200" and ms > 0
        ok, _, detalhe = Sonda("api", f"http://127.0.0.1:{porta_http}/quebrado").medir()
        assert not ok and "503" in detalhe
        ok, _, detalhe = Sonda("redis", f"redis://127.0.0.1:{redis_srv.getsockname()[1]}").medir()
        assert ok and detalhe == "+PONG"
    finally:
        http_srv.shutdown()
        redis_srv.close()

    livre = socket.socket()
    livre.bind(("127.0.0.1", 0))
    porta_fechada = livre.getsockname()[1]
    livre.close()
    ok, _, detalhe = Sonda("pg", f"pg://127.0.0.1:{porta_fechada}").medir(timeout=0.5)
    assert not ok and "Error" in detalhe


def test_sondas_da_config():
    s = sondas_da_config("api=http://api:8000/, redis=redis://redis")
    assert [x.nome for x in s] == ["api", "redis"]
    assert s[1].porta == 6379 and s[0].caminho == "/"
    assert sondas_da_config("") == []
