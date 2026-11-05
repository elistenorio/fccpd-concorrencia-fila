import pytest

from app.seed import gerar_dados
from recomendacoes.calculo import (
    BONUS_ARTESAO,
    BONUS_CATEGORIA,
    calcular,
    dividir_em_blocos,
)


def test_score_jaccard_com_bonus():
    compradores = [frozenset({1, 2, 3}), frozenset({2, 3, 4}), frozenset({9})]
    categorias = ["barro", "barro", "couro"]
    artesaos = ["A", "B", "C"]
    recs = calcular(compradores, categorias, artesaos, modo="sequencial", k=5)

    # 0 x 1: em comum {2, 3}, união {1, 2, 3, 4} -> 2/4, mesma categoria.
    assert recs[0] == [(1, pytest.approx(0.5 * (1 + BONUS_CATEGORIA)))]
    # 2 não tem nada em comum com ninguém: score 0 não vira recomendação.
    assert recs[2] == []


def test_bonus_sozinho_nao_recomenda_sem_comprador_em_comum():
    compradores = [frozenset({1}), frozenset({2})]
    recs = calcular(compradores, ["barro", "barro"], ["A", "A"], modo="sequencial")
    assert recs == [[], []]


def test_bonus_de_artesao_desempata():
    compradores = [frozenset({1, 2}), frozenset({1, 3}), frozenset({1, 4})]
    categorias = ["x", "y", "z"]
    artesaos = ["A", "B", "A"]
    recs = calcular(compradores, categorias, artesaos, modo="sequencial", k=2)
    # Jaccard igual (1/3) com 1 e 2, mas 2 é do mesmo artesão.
    assert [j for j, _ in recs[0]] == [2, 1]
    assert recs[0][0][1] == pytest.approx(1 / 3 * (1 + BONUS_ARTESAO))


def test_produto_nao_recomenda_ele_mesmo():
    compradores = [frozenset({1}), frozenset({1})]
    recs = calcular(compradores, ["c", "c"], ["a", "a"], modo="sequencial")
    assert [j for j, _ in recs[0]] == [1]
    assert [j for j, _ in recs[1]] == [0]


@pytest.mark.parametrize("n, n_blocos", [(10, 3), (3, 10), (100, 16), (1, 1)])
def test_blocos_cobrem_tudo_sem_sobrepor(n, n_blocos):
    blocos = dividir_em_blocos(n, n_blocos)
    assert blocos[0][0] == 0 and blocos[-1][1] == n
    assert all(blocos[i][1] == blocos[i + 1][0] for i in range(len(blocos) - 1))
    tamanhos = [f - i for i, f in blocos]
    assert max(tamanhos) - min(tamanhos) <= 1


def _dados_reais(n_produtos):
    produtos, pedidos = gerar_dados(n_produtos, n_produtos * 5, n_produtos * 40, semente=1)
    compradores = [set() for _ in produtos]
    for p in pedidos:
        compradores[p["produto"]].add(p["cliente_id"])
    return (
        [frozenset(c) for c in compradores],
        [p["categoria"] for p in produtos],
        [p["artesao"] for p in produtos],
    )


@pytest.mark.parametrize("modo", ["threads", "processos"])
def test_paralelo_da_o_mesmo_resultado_que_o_sequencial(modo):
    dados = _dados_reais(120)
    sequencial = calcular(*dados, modo="sequencial", k=10)
    paralelo = calcular(*dados, modo=modo, workers=3, k=10)
    assert paralelo == sequencial


def test_modo_invalido():
    with pytest.raises(ValueError):
        calcular([], [], [], modo="gpu")
