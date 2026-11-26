import os
import time

import pytest

from monitor.aed import ErroOperacao, Laboratorio, comparar_buscas, corrida_ordenacao
from monitor.corrida import executar_corrida


def test_laboratorio_chama_as_estruturas_reais():
    lab = Laboratorio()
    for v in (3, 1, 2):
        lab.operar("lista", "inserir_fim", v)
    assert lab.retrato("lista")["valores"] == [3, 1, 2]
    assert lab.operar("pilha", "empilhar", 5)["valores"] == [5]
    assert lab.operar("pilha", "desempilhar")["resultado"] == 5
    with pytest.raises(ErroOperacao):
        lab.operar("fila", "desenfileirar")


def test_laboratorio_fila_circular_da_a_volta():
    lab = Laboratorio()
    for v in range(8):
        lab.operar("anel", "enfileirar", v)
    with pytest.raises(ErroOperacao, match="cheia"):
        lab.operar("anel", "enfileirar", 8)
    lab.operar("anel", "desenfileirar")
    r = lab.operar("anel", "enfileirar", 8)
    assert r["inicio"] == 1 and r["posicoes"][0] == 8


def test_laboratorio_hash_redimensiona_e_arvore_degenera():
    lab = Laboratorio()
    for nome in ("vaso", "cesto", "rede", "manta"):
        r = lab.operar("hash", "inserir", nome)
    assert r["capacidade"] == 8  # 4 baldes; a 4ª chave passou de 0,75
    assert sum(len(b) for b in r["baldes"]) == 4
    r = lab.operar("arvore", "inserir_ordenado", 8)
    assert r["altura"] == 8
    r = lab.operar("arvore", "buscar", 50)
    assert r["resultado"] is True and r["caminho"] == [10, 20, 30, 40, 50]


def test_ordenacao_conta_comparacoes():
    r = corrida_ordenacao(200, "ordenada")
    assert all(x["correto"] for x in r["resultados"].values())
    assert r["resultados"]["insertion"]["comparacoes"] == 199  # melhor caso O(n)
    assert r["resultados"]["selection"]["comparacoes"] == 199 * 200 // 2


def test_busca_registra_indices_visitados():
    r = comparar_buscas(64)
    alvo_idx = r["valores"].index(r["alvo"])
    assert r["sequencial"]["indice"] == alvo_idx == len(r["sequencial"]["visitados"]) - 1
    assert r["binaria"]["indice"] == alvo_idx
    assert len(r["binaria"]["visitados"]) <= 7  # ceil(log2(64)) + 1


def test_corrida_com_lock_bate_e_sem_lock_perde():
    r = executar_corrida(produtores=3, consumidores=3, itens=1000, capacidade=8)
    assert r["com_lock"]["correto"] and r["com_lock"]["perdidas"] == 0
    assert r["sem_lock"]["retirados"] == 3000  # o buffer nunca perde item
    assert r["sem_lock"]["perdidas"] > 0


@pytest.mark.skipif(not os.path.exists("/proc/stat"), reason="precisa do /proc do Linux")
def test_coletor_pausado_enche_o_buffer():
    from monitor.coletor import Coletor

    coletor = Coletor(intervalo=0.05, capacidade_buffer=4)
    coletor.iniciar()
    try:
        time.sleep(0.6)
        r = coletor.instantaneo()
        assert r["total_processos"] > 0 and len(r["historico"]) > 0
        ultimo = r["historico"][-1]["ts"]
        assert len(coletor.instantaneo(desde=ultimo)["historico"]) <= 2  # só as novas

        coletor.pausar_consumidor(True)
        time.sleep(1.0)
        r = coletor.instantaneo()
        assert r["buffer"]["ocupacao"] == 4
        assert r["contadores"]["bloqueios"] > 0
        estados = {t["nome"]: t["estado"] for t in r["threads"]}
        assert estados["consumidor"] == "pausado"
        assert estados["produtor-sistema"].startswith("bloqueado")
    finally:
        coletor.parar()
