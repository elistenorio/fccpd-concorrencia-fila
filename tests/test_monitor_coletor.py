"""
Coletor concorrente: as threads, o buffer entre produtores e consumidor, e o
que acontece quando uma coleta falha.

Os coletores reais são substituídos por funções que devolvem amostras fixas,
então o teste não precisa de Docker, não lê ``/proc`` e não espera health check:
roda em menos de um segundo no CI.
"""

import threading
import time

from concorrencia.buffer import BufferLimitado
from monitor.coletor import Coletor
from monitor.estado import EstadoMonitor
from monitor.nivel2_containers import _cpu_pct, _memoria
from monitor.nivel3_servicos import parse_servicos


def _esperar(condicao, limite=5.0):
    """Espera a condição virar verdadeira (as threads são assíncronas)."""
    fim = time.time() + limite
    while time.time() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return False


def _coletor_de_teste(**kwargs):
    coletor = Coletor(estado=EstadoMonitor(), servicos=(), **kwargs)
    # Nada de Docker, /proc ou HTTP: cada nível devolve uma amostra fixa.
    coletor._so = type("FalsoSO", (), {"coletar": staticmethod(lambda: {"memoria": {"uso_pct": 10.0}})})()
    coletor._containers = type(
        "FalsoDocker",
        (),
        {
            "coletar": staticmethod(lambda: {"disponivel": True, "motivo": None, "containers": []}),
            "fechar": staticmethod(lambda: None),
        },
    )()
    coletor._servicos = type(
        "FalsoServicos",
        (),
        {"coletar": staticmethod(lambda: {"servicos": []}), "fechar": staticmethod(lambda: None)},
    )()
    return coletor


def test_coletor_sobe_quatro_threads_e_para_limpo():
    coletor = _coletor_de_teste()
    coletor.iniciar()
    try:
        nomes = {t["nome"] for t in coletor.threads_vivas()}
        assert nomes == {"monitor-nivel1", "monitor-nivel2", "monitor-nivel3", "monitor-consumidor"}
        assert all(t["viva"] for t in coletor.threads_vivas())
    finally:
        coletor.parar(timeout=3)
    assert coletor.threads_vivas() == []


def test_as_tres_threads_produzem_e_o_consumidor_aplica():
    coletor = _coletor_de_teste()
    coletor.iniciar()
    try:
        assert _esperar(lambda: all(coletor.estado.ultima(n) is not None for n in (1, 2, 3)))
        assert coletor.estatisticas["consumidor"]["aplicadas"] >= 3
    finally:
        coletor.parar(timeout=3)


def test_falha_em_uma_coleta_nao_mata_a_thread_e_vira_evento():
    coletor = _coletor_de_teste()

    chamadas = {"n": 0}

    def quebrado():
        chamadas["n"] += 1
        raise RuntimeError("socket do docker sumiu")

    coletor._containers = type(
        "Quebrado", (), {"coletar": staticmethod(quebrado), "fechar": staticmethod(lambda: None)}
    )()
    coletor.iniciar()
    try:
        # A thread continua tentando (mais de uma chamada) e o painel recebe o aviso.
        assert _esperar(lambda: coletor.estatisticas["nivel2"]["erros"] >= 1)
        assert _esperar(lambda: any("nível 2" in e.mensagem for e in coletor.estado.eventos()))
        # As outras duas continuam coletando normalmente.
        assert _esperar(lambda: coletor.estado.ultima(1) is not None)
        assert any(t["nome"] == "monitor-nivel2" and t["viva"] for t in coletor.threads_vivas())
    finally:
        coletor.parar(timeout=3)


def test_buffer_cheio_descarta_a_amostra_em_vez_de_travar_a_thread():
    # Buffer de 1 posição e nenhum consumidor rodando: a produtora precisa
    # desistir no timeout, senão o coletor inteiro trava.
    buffer = BufferLimitado(1)
    coletor = _coletor_de_teste(buffer=buffer)
    parar = threading.Event()
    coletor._parar = parar

    thread = threading.Thread(
        target=coletor._produzir, args=(1, lambda: {"memoria": {"uso_pct": 1.0}}, 0.05, "nivel1"), daemon=True
    )
    thread.start()
    try:
        assert _esperar(lambda: coletor.estatisticas["nivel1"]["erros"] >= 1, limite=3)
        assert "buffer cheio" in coletor.estatisticas["nivel1"]["ultimo_erro"]
    finally:
        parar.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_amostra_recebe_timestamp_e_duracao():
    coletor = _coletor_de_teste()
    coletor.iniciar()
    try:
        assert _esperar(lambda: coletor.estado.ultima(1) is not None)
        amostra = coletor.estado.ultima(1)
        assert amostra["timestamp"] > 0
        assert amostra["duracao_coleta_ms"] >= 0
    finally:
        coletor.parar(timeout=3)


# ------------------------------------------------- parses dos níveis 2 e 3


def test_cpu_de_container_e_a_diferenca_entre_as_duas_leituras_da_api():
    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 2_000_000_000},
            "system_cpu_usage": 20_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000_000},
            "system_cpu_usage": 18_000_000_000,
        },
    }
    # delta container = 1e9; delta sistema = 2e9; 4 núcleos -> 200%
    assert _cpu_pct(stats) == 200.0


def test_cpu_de_container_sem_leitura_anterior_fica_indefinida():
    assert _cpu_pct({"cpu_stats": {}, "precpu_stats": {}}) is None


def test_memoria_de_container_desconta_o_cache_de_arquivos():
    stats = {
        "memory_stats": {
            "usage": 300 * 1024**2,
            "limit": 1024 * 1024**2,
            "stats": {"inactive_file": 100 * 1024**2},
        }
    }
    memoria = _memoria(stats)
    # 300 MB de uso - 100 MB de cache = 200 MB reais de 1 GB
    assert memoria["usada_bytes"] == 200 * 1024**2
    assert memoria["uso_pct"] == 19.5


def test_parse_servicos_do_ambiente():
    assert parse_servicos("api=http://api:8000/, rec=http://rec:8001/") == (
        ("api", "http://api:8000/"),
        ("rec", "http://rec:8001/"),
    )
    assert parse_servicos("") == ()
    assert parse_servicos("sem_url") == ()


def test_descobre_sozinho_o_projeto_compose_do_proprio_monitor(tmp_path):
    # Numa máquina de desenvolvimento há containers de vários projetos; a tela 2
    # tem que mostrar só os do Origem, sem ninguém configurar nada. Dentro de um
    # container, o hostname é o começo do próprio id.
    from monitor.nivel2_containers import ColetorContainers

    hostname = tmp_path / "hostname"
    hostname.write_text("abc123def456\n", encoding="utf-8")
    lista = [
        {"Id": "abc123def4567890", "Labels": {"com.docker.compose.project": "origem"}},
        {"Id": "ffff00001111", "Labels": {"com.docker.compose.project": "outro"}},
    ]
    assert ColetorContainers._descobrir_projeto(lista, str(hostname)) == "origem"


def test_sem_container_correspondente_nao_filtra_nada(tmp_path):
    from monitor.nivel2_containers import ColetorContainers

    # Rodando fora do Docker, o hostname não casa com container nenhum:
    # melhor mostrar tudo que esconder tudo.
    hostname = tmp_path / "hostname"
    hostname.write_text("maquina-de-alguem\n", encoding="utf-8")
    lista = [{"Id": "0000", "Labels": {"com.docker.compose.project": "outro"}}]
    assert ColetorContainers._descobrir_projeto(lista, str(hostname)) is None
    # E sem o arquivo nenhum, também não quebra.
    assert ColetorContainers._descobrir_projeto(lista, str(tmp_path / "nao-existe")) is None


def test_projeto_informado_desliga_a_descoberta():
    from monitor.nivel2_containers import ColetorContainers

    assert ColetorContainers(rotulo_projeto="origem")._projeto_resolvido is True
    assert ColetorContainers(descobrir_projeto=False)._projeto_resolvido is True
    assert ColetorContainers()._projeto_resolvido is False
