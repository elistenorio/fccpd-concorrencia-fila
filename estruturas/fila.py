"""Fila (FIFO) encadeada, sem limite de tamanho."""


class _No:
    __slots__ = ("valor", "proximo")

    def __init__(self, valor):
        self.valor = valor
        self.proximo = None


class Fila:
    """
    enfileirar, desenfileirar e frente em O(1): entra pelo fim, sai pelo início.
    Para um buffer de tamanho fixo (ex.: histórico de métricas), ver FilaCircular.
    """

    def __init__(self):
        self._inicio = None
        self._fim = None
        self._tamanho = 0

    def __len__(self):
        return self._tamanho

    def __iter__(self):
        atual = self._inicio
        while atual is not None:
            yield atual.valor
            atual = atual.proximo

    def vazia(self) -> bool:
        return self._inicio is None

    def enfileirar(self, valor):
        no = _No(valor)
        if self._fim is None:
            self._inicio = self._fim = no
        else:
            self._fim.proximo = no
            self._fim = no
        self._tamanho += 1

    def desenfileirar(self):
        if self._inicio is None:
            raise IndexError("desenfileirar de fila vazia")
        no = self._inicio
        self._inicio = no.proximo
        if self._inicio is None:
            self._fim = None
        self._tamanho -= 1
        return no.valor

    def frente(self):
        if self._inicio is None:
            raise IndexError("frente de fila vazia")
        return self._inicio.valor
