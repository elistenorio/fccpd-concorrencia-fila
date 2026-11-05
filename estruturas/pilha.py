"""Pilha (LIFO) encadeada."""


class _No:
    __slots__ = ("valor", "abaixo")

    def __init__(self, valor, abaixo):
        self.valor = valor
        self.abaixo = abaixo


class Pilha:
    """empilhar, desempilhar e topo em O(1): tudo acontece no nó do topo."""

    def __init__(self):
        self._topo = None
        self._tamanho = 0

    def __len__(self):
        return self._tamanho

    def vazia(self) -> bool:
        return self._topo is None

    def empilhar(self, valor):
        self._topo = _No(valor, self._topo)
        self._tamanho += 1

    def desempilhar(self):
        if self._topo is None:
            raise IndexError("desempilhar de pilha vazia")
        no = self._topo
        self._topo = no.abaixo
        self._tamanho -= 1
        return no.valor

    def topo(self):
        if self._topo is None:
            raise IndexError("topo de pilha vazia")
        return self._topo.valor
