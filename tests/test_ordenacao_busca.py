import random

import pytest

from estruturas import (
    busca_binaria,
    busca_sequencial,
    insertion_sort,
    merge_sort,
    quick_sort,
    selection_sort,
)

ALGORITMOS = [insertion_sort, selection_sort, merge_sort, quick_sort]

ENTRADAS = {
    "vazia": [],
    "um": [7],
    "ordenada": list(range(100)),
    "invertida": list(range(100, 0, -1)),
    "repetidos": [3, 1, 3, 2, 1, 3, 2] * 10,
    "aleatoria": random.Random(7).sample(range(10_000), 500),
}


@pytest.mark.parametrize("ordenar", ALGORITMOS)
@pytest.mark.parametrize("nome", ENTRADAS)
def test_ordena_igual_ao_sorted(ordenar, nome):
    entrada = ENTRADAS[nome]
    copia = entrada[:]
    assert ordenar(entrada) == sorted(entrada)
    assert ordenar(entrada, decrescente=True) == sorted(entrada, reverse=True)
    assert entrada == copia  # não altera a entrada


@pytest.mark.parametrize("ordenar", ALGORITMOS)
def test_ordena_com_chave(ordenar):
    # Formato que o top-K de recomendações vai usar: (produto_id, score).
    pares = [(1, 0.2), (2, 0.9), (3, 0.5), (4, 0.7)]
    resultado = ordenar(pares, chave=lambda p: p[1], decrescente=True)
    assert [p[0] for p in resultado] == [2, 4, 3, 1]


@pytest.mark.parametrize("ordenar", [insertion_sort, merge_sort])
def test_algoritmos_estaveis_preservam_ordem_dos_empates(ordenar):
    pares = [("a", 1), ("b", 0), ("c", 1), ("d", 0)]
    resultado = ordenar(pares, chave=lambda p: p[1])
    assert [p[0] for p in resultado] == ["b", "d", "a", "c"]


@pytest.mark.parametrize("ordenar", ALGORITMOS)
def test_aceita_gerador(ordenar):
    assert ordenar(x % 5 for x in range(10)) == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_busca_sequencial():
    dados = [5, 3, 8, 3]
    assert busca_sequencial(dados, 3) == 1
    assert busca_sequencial(dados, 9) == -1
    assert busca_sequencial([], 1) == -1


def test_busca_binaria():
    dados = list(range(0, 200, 2))
    for i, valor in enumerate(dados):
        assert busca_binaria(dados, valor) == i
    assert busca_binaria(dados, 3) == -1
    assert busca_binaria(dados, -1) == -1
    assert busca_binaria(dados, 1000) == -1
    assert busca_binaria([], 1) == -1


def test_busca_binaria_devolve_primeira_ocorrencia():
    assert busca_binaria([1, 2, 2, 2, 3], 2) == 1


def test_busca_com_chave():
    produtos = merge_sort([(30, "c"), (10, "a"), (20, "b")], chave=lambda p: p[0])
    assert busca_binaria(produtos, 20, chave=lambda p: p[0]) == 1
    assert busca_sequencial(produtos, "c", chave=lambda p: p[1]) == 2
