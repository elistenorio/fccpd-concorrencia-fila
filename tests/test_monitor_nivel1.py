"""
Parse do nível 1 (``/proc``) com amostras fixas.

Os parses recebem texto, então o teste não depende de Linux, nem de quais
processos estão rodando, nem da carga da máquina: dá o mesmo resultado sempre.
"""

import pytest

from monitor.nivel1_so import (
    ColetorSO,
    parse_meminfo,
    parse_proc_stat_processo,
    parse_stat_cpus,
    parse_uptime,
)

STAT = """cpu  100 0 50 800 50 0 0 0 0 0
cpu0 60 0 30 400 10 0 0 0 0 0
cpu1 40 0 20 400 40 0 0 0 0 0
intr 12345 0 0
ctxt 99999
"""

STAT_DEPOIS = """cpu  200 0 100 1400 50 0 0 0 0 0
cpu0 160 0 80 600 10 0 0 0 0 0
cpu1 40 0 20 800 40 0 0 0 0 0
intr 22345 0 0
ctxt 199999
"""

MEMINFO = """MemTotal:       16000000 kB
MemFree:         1000000 kB
MemAvailable:    4000000 kB
Buffers:          200000 kB
SwapTotal:       2000000 kB
SwapFree:        1500000 kB
"""


def test_parse_stat_cpus_separa_agregado_e_nucleos():
    cpus = parse_stat_cpus(STAT)
    assert [nome for nome, _, _ in cpus] == ["cpu", "cpu0", "cpu1"]
    # cpu0: total = 60+30+400+10 = 500; idle+iowait = 410; ocupado = 90
    nome, ocupado, total = cpus[1]
    assert (nome, ocupado, total) == ("cpu0", 90, 500)


def test_parse_stat_cpus_ignora_linhas_que_nao_sao_cpu():
    assert all(nome.startswith("cpu") for nome, _, _ in parse_stat_cpus(STAT))


def test_parse_meminfo_converte_para_bytes_e_calcula_uso():
    memoria = parse_meminfo(MEMINFO)
    assert memoria["total_bytes"] == 16000000 * 1024
    assert memoria["disponivel_bytes"] == 4000000 * 1024
    # usada = total - disponível = 12 GB de 16 GB = 75%
    assert memoria["uso_pct"] == 75.0
    assert memoria["swap_usada_bytes"] == 500000 * 1024


def test_parse_meminfo_usa_memfree_quando_nao_ha_memavailable():
    sem_available = "MemTotal: 1000 kB\nMemFree: 400 kB\n"
    assert parse_meminfo(sem_available)["disponivel_bytes"] == 400 * 1024


def test_parse_processo_com_parenteses_e_espacos_no_nome():
    # O nome do processo pode conter espaços e parênteses: os campos têm que
    # ser separados a partir do ÚLTIMO ")", não por split simples.
    linha = (
        "4242 (postgres: (logger)) S 1 1 1 0 -1 4194560 100 0 0 0 "
        "70 30 0 0 20 0 7 0 500 1234567 2048 "
        + " ".join(["0"] * 30)
    )
    processo = parse_proc_stat_processo(linha)
    assert processo["pid"] == 4242
    assert processo["nome"] == "postgres: (logger)"
    assert processo["estado"] == "dormindo"
    assert processo["ticks_cpu"] == 100  # utime 70 + stime 30
    assert processo["threads"] == 7
    assert processo["rss_bytes"] > 0


def test_parse_uptime():
    assert parse_uptime("12345.67 98765.43\n") == 12345.67


def test_primeira_amostra_de_cpu_nao_tem_percentual():
    # /proc/stat traz contadores acumulados: sem uma leitura anterior não existe
    # percentual, e inventar 0% esconderia isso.
    coletor = ColetorSO()
    assert coletor._uso_cpu(parse_stat_cpus(STAT)) == [
        {"nome": "cpu", "uso_pct": None},
        {"nome": "cpu0", "uso_pct": None},
        {"nome": "cpu1", "uso_pct": None},
    ]


def test_percentual_de_cpu_vem_da_diferenca_entre_duas_leituras():
    coletor = ColetorSO()
    coletor._uso_cpu(parse_stat_cpus(STAT))  # primeira leitura, guarda os contadores
    coletor._cpus_anterior = parse_stat_cpus(STAT)
    uso = coletor._uso_cpu(parse_stat_cpus(STAT_DEPOIS))
    por_nome = {item["nome"]: item["uso_pct"] for item in uso}
    # cpu0: ocupado 90 -> 240 (delta 150), total 500 -> 850 (delta 350) = 42,9%
    assert por_nome["cpu0"] == pytest.approx(42.9, abs=0.1)
    # cpu1 não trabalhou: só o idle subiu.
    assert por_nome["cpu1"] == 0.0


def test_percentual_de_cpu_fica_entre_0_e_100():
    coletor = ColetorSO()
    coletor._cpus_anterior = parse_stat_cpus(STAT)
    # Contadores que "andaram para trás" (acontece se o host hiberna) não podem
    # virar percentual negativo na tela.
    estranho = "cpu0 10 0 5 10 0 0 0 0 0 0\n"
    uso = coletor._uso_cpu(parse_stat_cpus(estranho))
    assert 0.0 <= uso[0]["uso_pct"] <= 100.0
