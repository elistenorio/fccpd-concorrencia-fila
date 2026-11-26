"""Modelo de domínio (AED): estoque, pedidos e desfazer, com casos simples e dados sintéticos."""

import random
from datetime import date, timedelta

import pytest

from dominio import ATENDIDO, CANCELADO, PENDENTE, RECUSADO, ErroEstoque, Loja
from estruturas import ListaOrdenada, limite_inferior

HOJE = date(2026, 11, 26)


def loja_basica(prioridade=False):
    loja = Loja(prioridade=prioridade)
    loja.cadastrar("VAS-01", "Vaso de barro", "cerâmica", "Dona Ana", 8900)
    loja.cadastrar("CES-01", "Cesto de palha", "trançado", "Seu João", 4500)
    loja.cadastrar("VAS-02", "Vaso pintado", "cerâmica", "Dona Ana", 12000)
    loja.cadastrar("DOC-01", "Doce de caju", "alimento", "Dona Lia", 1500)
    return loja


# ------------------------------------------------------------------ estruturas novas

def test_lista_ordenada_estavel_e_atalho_no_fim():
    lista = ListaOrdenada(chave=lambda par: par[0])
    for par in [(3, "a"), (1, "b"), (3, "c"), (2, "d"), (5, "e")]:
        lista.inserir(par)
    assert [v for _, v in lista] == ["b", "d", "a", "c", "e"]  # empate 3: a antes de c
    assert lista.inserir((9, "f")) == 1  # maior que o último: O(1) pelo _fim
    assert lista.remover_inicio() == (1, "b")
    alvo = lista.para_array()[2]
    assert lista.remover(alvo) and len(lista) == 4
    assert lista.inserir((10, "g")) == 1 and lista.para_array()[-1] == (10, "g")  # _fim continua certo


def test_limite_inferior():
    a = [10, 20, 20, 30]
    assert [limite_inferior(a, x) for x in (5, 10, 20, 25, 30, 99)] == [0, 0, 1, 3, 3, 4]
    assert limite_inferior([], 1) == 0


# ------------------------------------------------------------------ estoque

def test_cadastro_consulta_e_remocao():
    loja = loja_basica()
    assert loja.estoque.consultar("CES-01").nome == "Cesto de palha"
    assert loja.estoque.consultar_sequencial("CES-01") is loja.estoque.consultar("CES-01")
    with pytest.raises(ErroEstoque, match="já cadastrado"):
        loja.cadastrar("CES-01", "outro")
    loja.remover_produto("CES-01")
    assert len(loja.estoque) == 3 and not loja.estoque.existe("CES-01")
    assert loja.estoque.buscar_prefixo("cesto") == []
    with pytest.raises(ErroEstoque, match="não existe"):
        loja.estoque.consultar("CES-01")


def test_busca_por_nome_prefixo_e_faixa_de_preco():
    loja = loja_basica()
    assert [p.codigo for p in loja.estoque.buscar_prefixo("VASO")] == ["VAS-01", "VAS-02"]  # ordem alfabética
    assert [p.codigo for p in loja.estoque.buscar_nome("doce de caju")] == ["DOC-01"]
    assert [p.codigo for p in loja.estoque.faixa_de_preco(4000, 9000)] == ["CES-01", "VAS-01"]
    assert loja.estoque.faixa_de_preco(20000, 30000) == []
    loja.alterar_preco("DOC-01", 5000)  # invalida o catálogo ordenado
    assert [p.codigo for p in loja.estoque.faixa_de_preco(4000, 9000)] == ["CES-01", "DOC-01", "VAS-01"]


def test_saida_fefo_pula_vencidos_e_e_tudo_ou_nada():
    loja = loja_basica()
    loja.entrada("DOC-01", 5, HOJE + timedelta(days=30))
    loja.entrada("DOC-01", 4, HOJE + timedelta(days=5))   # vence antes: entra na frente
    loja.entrada("DOC-01", 2, HOJE - timedelta(days=1))   # já vencido
    doce = loja.estoque.consultar("DOC-01")
    assert [l.quantidade for l in doce.lotes] == [2, 4, 5]
    assert doce.disponivel(HOJE) == 9

    loja.saida("DOC-01", 6, hoje=HOJE)  # 4 do que vence em 5 dias + 2 do de 30 dias
    assert [(l.quantidade, l.validade) for l in doce.lotes] == [(2, HOJE - timedelta(days=1)), (3, HOJE + timedelta(days=30))]
    with pytest.raises(ErroEstoque, match="insuficiente"):
        loja.saida("DOC-01", 4, hoje=HOJE)
    assert doce.quantidade == 5  # nada saiu
    assert [(p.codigo, l.quantidade) for p, l in loja.estoque.vencidos(HOJE)] == [("DOC-01", 2)]


def test_lote_sem_validade_sai_por_ultimo():
    loja = loja_basica()
    loja.entrada("VAS-01", 3)  # não perecível
    loja.entrada("VAS-01", 1, HOJE + timedelta(days=400))
    assert [l.validade for l in loja.estoque.consultar("VAS-01").lotes] == [HOJE + timedelta(days=400), None]


# ------------------------------------------------------------------ pedidos

def test_fila_fifo_atende_por_ordem_de_chegada():
    loja = loja_basica()
    loja.entrada("CES-01", 3)
    p1 = loja.registrar_pedido("CES-01", 2)
    p2 = loja.registrar_pedido("CES-01", 2, prioridade=9)  # ignorada na FIFO
    p3 = loja.registrar_pedido("CES-01", 1)
    assert [p.id for p in loja.pedidos] == [p1.id, p2.id, p3.id]
    assert loja.processar_todos() == (2, 1)
    assert (p1.status, p2.status, p3.status) == (ATENDIDO, RECUSADO, ATENDIDO)
    assert "insuficiente" in p2.motivo
    assert loja.estoque.consultar("CES-01").quantidade == 0


def test_fila_com_prioridade_e_empate_por_chegada():
    loja = loja_basica(prioridade=True)
    loja.entrada("VAS-01", 10)
    a = loja.registrar_pedido("VAS-01", 1, prioridade=1)
    b = loja.registrar_pedido("VAS-01", 1, prioridade=5)
    c = loja.registrar_pedido("VAS-01", 1, prioridade=1)
    d = loja.registrar_pedido("VAS-01", 1, prioridade=5)
    assert [p.id for p in loja.pedidos] == [b.id, d.id, a.id, c.id]
    assert [loja.processar_proximo().id for _ in range(4)] == [b.id, d.id, a.id, c.id]
    assert loja.processar_proximo() is None


def test_pedido_de_produto_inexistente():
    loja = loja_basica()
    with pytest.raises(ErroEstoque):
        loja.registrar_pedido("NAO-EXISTE", 1)


# ------------------------------------------------------------------ desfazer

def retrato(loja):
    """Estado completo comparável: produtos, lotes, preços e pedidos pendentes."""
    produtos = tuple((p.codigo, p.nome, p.preco_centavos, tuple((l.quantidade, l.validade) for l in p.lotes))
                     for p in loja.estoque)
    pendentes = tuple((p.id, p.status) for p in loja.pedidos)
    return produtos, pendentes, len(loja.pedidos)


@pytest.mark.parametrize("prioridade", [False, True])
def test_desfazer_volta_exatamente_cada_estado(prioridade):
    loja = Loja(prioridade=prioridade)
    estados = [retrato(loja)]
    passos = [
        lambda: loja.cadastrar("A", "Arte", preco_centavos=100),
        lambda: loja.cadastrar("B", "Bolsa", preco_centavos=300),
        lambda: loja.entrada("A", 5, HOJE + timedelta(days=10)),
        lambda: loja.entrada("A", 2, HOJE + timedelta(days=2)),
        lambda: loja.entrada("B", 1),
        lambda: loja.alterar_preco("B", 250),
        lambda: loja.registrar_pedido("A", 3, prioridade=1),
        lambda: loja.registrar_pedido("B", 5, prioridade=2),   # vai ser recusado
        lambda: loja.registrar_pedido("A", 1),
        lambda: loja.processar_proximo(HOJE),
        lambda: loja.processar_proximo(HOJE),
        lambda: loja.saida("A", 1, HOJE),
        lambda: loja.cancelar_pedido(next(iter(loja.pedidos))),
        lambda: loja.remover_produto("B"),
    ]
    for passo in passos:
        passo()
        estados.append(retrato(loja))
    assert len(loja.historico) == len(passos)
    for esperado in reversed(estados[:-1]):
        loja.desfazer()
        assert retrato(loja) == esperado
    with pytest.raises(ErroEstoque, match="nada para desfazer"):
        loja.desfazer()


def test_desfazer_processamento_recoloca_na_frente_na_ordem_original():
    loja = loja_basica()
    loja.entrada("VAS-01", 10)
    pedidos = [loja.registrar_pedido("VAS-01", 1) for _ in range(4)]
    loja.processar_proximo()
    loja.processar_proximo()
    loja.desfazer()
    loja.desfazer()
    assert [p.id for p in loja.pedidos] == [p.id for p in pedidos]
    assert loja.estoque.consultar("VAS-01").quantidade == 10
    assert [loja.processar_proximo().id for _ in range(4)] == [p.id for p in pedidos]


def test_ultimas_operacoes_nao_mexe_na_pilha():
    loja = loja_basica()
    assert [op.tipo for op in loja.ultimas_operacoes(2)] == ["cadastrar", "cadastrar"]
    assert loja.ultimas_operacoes(1)[0].descricao == "cadastrou DOC-01"
    assert len(loja.historico) == 4


def test_cancelar_pedido():
    loja = loja_basica()
    loja.entrada("CES-01", 5)
    p = loja.registrar_pedido("CES-01", 1)
    loja.cancelar_pedido(p)
    assert p.status == CANCELADO and len(loja.pedidos) == 0
    assert loja.processar_proximo() is None  # o cancelado é pulado
    loja.desfazer()  # processar sem pedido não empilha nada: isto desfaz o cancelamento
    assert p.status == PENDENTE and len(loja.pedidos) == 1


# ------------------------------------------------------------------ volumes sintéticos

@pytest.mark.parametrize("n", [100, 1000, 10000])
def test_volumes_sinteticos_batem_com_a_versao_ingenua(n):
    """Estoque com n produtos aleatórios: cada busca rápida devolve o mesmo que a varredura."""
    rnd = random.Random(n)
    loja = Loja()
    nomes = ("vaso", "cesto", "renda", "doce", "bolsa", "colar", "rede", "prato")
    produtos = []
    for i in range(n):
        codigo = f"P{rnd.randrange(10**9):09d}-{i}"
        nome = f"{rnd.choice(nomes)} {rnd.randrange(1000)}"
        preco = rnd.randrange(500, 50000)
        loja.cadastrar(codigo, nome, preco_centavos=preco)
        produtos.append((codigo, nome, preco))

    for codigo, _, _ in rnd.sample(produtos, 50):
        assert loja.estoque.consultar(codigo) is loja.estoque.consultar_sequencial(codigo)

    for prefixo in ("vaso 1", "rede", "colar 99"):
        rapido = [p.codigo for p in loja.estoque.buscar_prefixo(prefixo)]
        ingenuo = [c for _, c in sorted((nm, c) for c, nm, _ in produtos if nm.startswith(prefixo))]
        assert rapido == ingenuo

    for minimo, maximo in ((500, 600), (10000, 12000), (49000, 60000)):
        rapido = sorted(p.codigo for p in loja.estoque.faixa_de_preco(minimo, maximo))
        ingenuo = sorted(c for c, _, pr in produtos if minimo <= pr <= maximo)
        assert rapido == ingenuo

    # muitos lotes em ordem aleatória de validade: a lista sai sempre ordenada
    codigo = produtos[0][0]
    for _ in range(200):
        loja.entrada(codigo, rnd.randrange(1, 10), HOJE + timedelta(days=rnd.randrange(-30, 365)))
    validades = [l.validade for l in loja.estoque.consultar(codigo).lotes]
    assert validades == sorted(validades)
