from collections import Counter

from app.seed import CATEGORIAS, gerar_dados


def test_mesma_semente_gera_os_mesmos_dados():
    a = gerar_dados(100, 200, 1000, semente=7)
    b = gerar_dados(100, 200, 1000, semente=7)
    assert a[0] == b[0]
    assert [(p["produto"], p["cliente_id"]) for p in a[1]] == [(p["produto"], p["cliente_id"]) for p in b[1]]


def test_volumes_e_referencias_validas():
    produtos, pedidos = gerar_dados(50, 300, 2000)
    assert len(produtos) == 50
    assert len(pedidos) == 2000
    assert all(0 <= p["produto"] < 50 for p in pedidos)
    assert all(1 <= p["cliente_id"] <= 300 for p in pedidos)
    assert all(p["categoria"] in CATEGORIAS for p in produtos)


def test_artesao_trabalha_com_uma_categoria_so():
    produtos, _ = gerar_dados(500, 10, 10)
    categorias = {}
    for p in produtos:
        categorias.setdefault(p["artesao"], set()).add(p["categoria"])
    assert all(len(c) == 1 for c in categorias.values())


def test_clientes_concentram_compras_nas_categorias_preferidas():
    # Se as compras fossem aleatórias, cada cliente compraria em ~todas as 10
    # categorias; com preferência, a categoria mais comprada domina.
    produtos, pedidos = gerar_dados(500, 100, 20000)
    por_cliente = {}
    for p in pedidos:
        por_cliente.setdefault(p["cliente_id"], Counter())[produtos[p["produto"]]["categoria"]] += 1
    frequentes = [c for c in por_cliente.values() if sum(c.values()) >= 50]
    assert frequentes
    participacao_top2 = sum(sum(n for _, n in c.most_common(2)) / sum(c.values()) for c in frequentes) / len(frequentes)
    assert participacao_top2 > 0.7
