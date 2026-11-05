"""
Estruturas de dados, ordenação e busca implementadas pela equipe (AED).

Regra do PI3: nas partes avaliadas não usamos as coleções prontas do Python
(append/pop/insert de list, dict, set, deque, sorted, bisect...). A única coisa
nativa usada é um bloco de tamanho fixo ``[None] * n`` como "array" (acesso por
índice), que é o equivalente ao vetor alocado de C/Java.
"""

from estruturas.arvore_busca import ArvoreBusca
from estruturas.busca import busca_binaria, busca_sequencial
from estruturas.fila import Fila
from estruturas.fila_circular import FilaCircular
from estruturas.lista import ListaEncadeada
from estruturas.ordenacao import insertion_sort, merge_sort, quick_sort, selection_sort
from estruturas.pilha import Pilha
from estruturas.tabela_hash import TabelaHash
from estruturas.top_k import TopK

__all__ = [
    "ArvoreBusca",
    "Fila",
    "FilaCircular",
    "ListaEncadeada",
    "Pilha",
    "TabelaHash",
    "TopK",
    "busca_binaria",
    "busca_sequencial",
    "insertion_sort",
    "merge_sort",
    "quick_sort",
    "selection_sort",
]
