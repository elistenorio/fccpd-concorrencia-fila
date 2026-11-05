"""Fila circular (ring buffer) sobre um array de tamanho fixo."""


class FilaCheiaError(Exception):
    pass


class FilaCircular:
    """
    Fila de capacidade fixa: ``_inicio`` aponta para o mais antigo e os índices
    "dão a volta" com módulo, então nenhuma operação desloca elementos.

    Complexidade: enfileirar, desenfileirar e frente em O(1); memória O(capacidade),
    alocada uma única vez. Por isso é a estrutura do monitor: o histórico de
    métricas nunca cresce além do limite, não importa por quanto tempo rode.

    sobrescrever=False -> fila cheia levanta FilaCheiaError (buffer produtor-consumidor).
    sobrescrever=True  -> fila cheia descarta o mais antigo (janela das últimas N amostras).
    """

    def __init__(self, capacidade: int, sobrescrever: bool = False):
        if capacidade < 1:
            raise ValueError("capacidade deve ser >= 1")
        self._dados = [None] * capacidade
        self._capacidade = capacidade
        self._inicio = 0
        self._tamanho = 0
        self._sobrescrever = sobrescrever

    def __len__(self):
        return self._tamanho

    def __iter__(self):
        """Do mais antigo para o mais novo."""
        for i in range(self._tamanho):
            yield self._dados[(self._inicio + i) % self._capacidade]

    @property
    def capacidade(self) -> int:
        return self._capacidade

    def vazia(self) -> bool:
        return self._tamanho == 0

    def cheia(self) -> bool:
        return self._tamanho == self._capacidade

    def enfileirar(self, valor):
        if self.cheia():
            if not self._sobrescrever:
                raise FilaCheiaError("fila circular cheia")
            # Descarta o mais antigo: o novo ocupa a posição dele e o início avança.
            self._dados[self._inicio] = valor
            self._inicio = (self._inicio + 1) % self._capacidade
            return
        fim = (self._inicio + self._tamanho) % self._capacidade
        self._dados[fim] = valor
        self._tamanho += 1

    def desenfileirar(self):
        if self._tamanho == 0:
            raise IndexError("desenfileirar de fila vazia")
        valor = self._dados[self._inicio]
        self._dados[self._inicio] = None  # solta a referência para o GC
        self._inicio = (self._inicio + 1) % self._capacidade
        self._tamanho -= 1
        return valor

    def frente(self):
        if self._tamanho == 0:
            raise IndexError("frente de fila vazia")
        return self._dados[self._inicio]
