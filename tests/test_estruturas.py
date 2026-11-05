import random

import pytest

from estruturas import ArvoreBusca, Fila, FilaCircular, ListaEncadeada, Pilha, TabelaHash, TopK
from estruturas.fila_circular import FilaCheiaError


# --- Lista encadeada ---

def test_lista_insere_no_inicio_e_no_fim():
    lista = ListaEncadeada()
    lista.inserir_fim(2)
    lista.inserir_fim(3)
    lista.inserir_inicio(1)
    assert [*lista] == [1, 2, 3]
    assert len(lista) == 3
    assert lista.obter(2) == 3


def test_lista_remove_inicio_meio_e_fim():
    lista = ListaEncadeada()
    for v in (1, 2, 3, 4):
        lista.inserir_fim(v)
    assert lista.remover(1)
    assert lista.remover(3)
    assert lista.remover(4)
    assert not lista.remover(99)
    assert [*lista] == [2]
    # O ponteiro de fim precisa continuar certo depois de remover o último.
    lista.inserir_fim(5)
    assert [*lista] == [2, 5]


def test_lista_vazia():
    lista = ListaEncadeada()
    assert lista.vazia()
    with pytest.raises(IndexError):
        lista.remover_inicio()
    with pytest.raises(IndexError):
        lista.obter(0)


# --- Pilha ---

def test_pilha_lifo():
    pilha = Pilha()
    for v in (1, 2, 3):
        pilha.empilhar(v)
    assert pilha.topo() == 3
    assert [pilha.desempilhar() for _ in range(3)] == [3, 2, 1]
    assert pilha.vazia()
    with pytest.raises(IndexError):
        pilha.desempilhar()


# --- Fila ---

def test_fila_fifo():
    fila = Fila()
    for v in (1, 2, 3):
        fila.enfileirar(v)
    assert fila.frente() == 1
    assert [fila.desenfileirar() for _ in range(3)] == [1, 2, 3]
    assert fila.vazia()
    with pytest.raises(IndexError):
        fila.desenfileirar()


# --- Fila circular ---

def test_fila_circular_da_a_volta_no_array():
    fila = FilaCircular(3)
    fila.enfileirar(1)
    fila.enfileirar(2)
    assert fila.desenfileirar() == 1
    fila.enfileirar(3)
    fila.enfileirar(4)  # ocupa a posição 0 de novo
    assert fila.cheia()
    assert [*fila] == [2, 3, 4]
    assert [fila.desenfileirar() for _ in range(3)] == [2, 3, 4]


def test_fila_circular_cheia_levanta_erro():
    fila = FilaCircular(2)
    fila.enfileirar(1)
    fila.enfileirar(2)
    with pytest.raises(FilaCheiaError):
        fila.enfileirar(3)


def test_fila_circular_sobrescreve_o_mais_antigo():
    janela = FilaCircular(3, sobrescrever=True)
    for v in range(1, 6):
        janela.enfileirar(v)
    assert len(janela) == 3
    assert [*janela] == [3, 4, 5]
    assert janela.frente() == 3


# --- Tabela hash ---

def test_hash_insere_atualiza_e_remove():
    tabela = TabelaHash()
    tabela["a"] = 1
    tabela["b"] = 2
    tabela["a"] = 10
    assert len(tabela) == 2
    assert tabela["a"] == 10
    assert tabela.remover("b") == 2
    assert "b" not in tabela
    assert tabela.obter("b", None) is None
    with pytest.raises(KeyError):
        tabela["b"]


def test_hash_redimensiona_sem_perder_chaves():
    tabela = TabelaHash(capacidade_inicial=4)
    for i in range(1000):
        tabela[i] = i * i
    assert len(tabela) == 1000
    assert all(tabela[i] == i * i for i in range(1000))
    assert sorted(tabela.chaves()) == list(range(1000))


def test_hash_com_colisoes():
    # Com 1 balde só, tudo colide: a tabela vira uma lista, mas continua correta.
    tabela = TabelaHash(capacidade_inicial=1)
    tabela.FATOR_CARGA_MAXIMO = float("inf")
    for chave in ("x", "y", "z"):
        tabela[chave] = chave.upper()
    assert tabela.remover("y") == "Y"
    assert tabela["x"] == "X" and tabela["z"] == "Z"


def test_hash_chave_tupla():
    tabela = TabelaHash()
    tabela[(1, 2)] = "par"
    assert tabela[(1, 2)] == "par"
    assert (2, 1) not in tabela


# --- Árvore binária de busca ---

def test_arvore_em_ordem_devolve_chaves_ordenadas():
    arvore = ArvoreBusca()
    chaves = list(range(200))
    random.Random(42).shuffle(chaves)
    for c in chaves:
        arvore.inserir(c, str(c))
    assert [c for c, _ in arvore.em_ordem()] == list(range(200))
    assert arvore.minimo() == 0 and arvore.maximo() == 199
    assert arvore.buscar(57) == "57"
    assert arvore.buscar(500) is None


@pytest.mark.parametrize("removida", [50, 30, 70, 20, 80])
def test_arvore_remove_folha_um_filho_e_dois_filhos(removida):
    arvore = ArvoreBusca()
    for c in (50, 30, 70, 20, 40, 60, 80, 85):
        arvore.inserir(c)
    assert arvore.remover(removida)
    assert removida not in arvore
    esperado = sorted({50, 30, 70, 20, 40, 60, 80, 85} - {removida})
    assert [c for c, _ in arvore.em_ordem()] == esperado
    assert len(arvore) == len(esperado)


def test_arvore_degenera_com_chaves_ordenadas():
    # Caso que justifica a análise O(h): inserção ordenada vira uma "lista".
    arvore = ArvoreBusca()
    for c in range(1000):
        arvore.inserir(c)
    assert arvore.altura() == 1000

    aleatoria = ArvoreBusca()
    chaves = list(range(1000))
    random.Random(1).shuffle(chaves)
    for c in chaves:
        aleatoria.inserir(c)
    assert aleatoria.altura() < 50  # ~ 2 a 3 vezes log2(1000) ≈ 10


# --- Top-K ---

def test_top_k_igual_a_ordenar_e_cortar():
    rng = random.Random(3)
    dados = [(i, rng.random()) for i in range(1000)]
    top = TopK(10)
    for item, score in dados:
        top.oferecer(item, score)
    esperado = sorted(dados, key=lambda p: p[1], reverse=True)[:10]
    assert top.resultado() == esperado


def test_top_k_com_menos_itens_que_k():
    top = TopK(5)
    for item, score in [("a", 0.1), ("b", 0.9), ("c", 0.5)]:
        top.oferecer(item, score)
    assert top.resultado() == [("b", 0.9), ("c", 0.5), ("a", 0.1)]
    assert len(top) == 3


def test_top_k_empate_mantem_quem_chegou_antes():
    top = TopK(2)
    for item in ("a", "b", "c"):
        top.oferecer(item, 1.0)
    assert top.resultado() == [("a", 1.0), ("b", 1.0)]
