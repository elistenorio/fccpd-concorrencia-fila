"""Lista simplesmente encadeada com ponteiro para o fim."""


class _No:
    __slots__ = ("valor", "proximo")

    def __init__(self, valor, proximo=None):
        self.valor = valor
        self.proximo = proximo


class ListaEncadeada:
    """
    Complexidade:
    - inserir_inicio, inserir_fim, remover_inicio: O(1)
    - obter(i), remover(valor), contem(valor): O(n)

    O ponteiro ``_fim`` é o que deixa inserir_fim em O(1); sem ele seria
    preciso percorrer a lista inteira a cada inserção.
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
        return self._tamanho == 0

    def inserir_inicio(self, valor):
        self._inicio = _No(valor, self._inicio)
        if self._fim is None:
            self._fim = self._inicio
        self._tamanho += 1

    def inserir_fim(self, valor):
        no = _No(valor)
        if self._fim is None:
            self._inicio = self._fim = no
        else:
            self._fim.proximo = no
            self._fim = no
        self._tamanho += 1

    def remover_inicio(self):
        if self._inicio is None:
            raise IndexError("remover de lista vazia")
        no = self._inicio
        self._inicio = no.proximo
        if self._inicio is None:
            self._fim = None
        self._tamanho -= 1
        return no.valor

    def remover(self, valor) -> bool:
        """Remove a primeira ocorrência de ``valor``. Retorna se achou."""
        anterior = None
        atual = self._inicio
        while atual is not None:
            if atual.valor == valor:
                if anterior is None:
                    self._inicio = atual.proximo
                else:
                    anterior.proximo = atual.proximo
                if atual is self._fim:
                    self._fim = anterior
                self._tamanho -= 1
                return True
            anterior = atual
            atual = atual.proximo
        return False

    def obter(self, indice: int):
        if indice < 0 or indice >= self._tamanho:
            raise IndexError("índice fora da lista")
        atual = self._inicio
        for _ in range(indice):
            atual = atual.proximo
        return atual.valor

    def contem(self, valor) -> bool:
        for item in self:
            if item == valor:
                return True
        return False

    def para_array(self):
        """Copia os valores para um array de tamanho fixo (usado pela ordenação)."""
        array = [None] * self._tamanho
        i = 0
        for valor in self:
            array[i] = valor
            i += 1
        return array
