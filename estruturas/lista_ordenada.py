"""Lista encadeada mantida em ordem por inserção ordenada."""


class _No:
    __slots__ = ("valor", "chave", "proximo")

    def __init__(self, valor, chave, proximo=None):
        self.valor = valor
        self.chave = chave
        self.proximo = proximo


class ListaOrdenada:
    """
    Cada inserção percorre a lista até achar o lugar do novo nó, então a lista
    está sempre em ordem crescente de ``chave(valor)`` e o menor sai em O(1).

    - inserir: O(n) no pior caso; O(1) se a chave for >= à do último (atalho
      pelo ponteiro _fim: é o caso comum de itens que chegam já em ordem)
    - remover_inicio / primeiro: O(1)
    - remover(valor): O(n), compara por identidade (``is``)

    Estável: chaves iguais ficam na ordem de chegada (o novo entra DEPOIS dos
    iguais). É o que deixa usar a mesma lista como fila de prioridade em que
    empates são atendidos por ordem de chegada.
    """

    def __init__(self, chave=None):
        self._chave = chave if chave is not None else (lambda x: x)
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

    def inserir(self, valor):
        """Insere na posição certa. Devolve quantos nós foram percorridos (para análise)."""
        k = self._chave(valor)
        self._tamanho += 1
        if self._inicio is None:
            self._inicio = self._fim = _No(valor, k)
            return 0
        if not k < self._fim.chave:  # atalho: vai para o fim
            self._fim.proximo = _No(valor, k)
            self._fim = self._fim.proximo
            return 1
        if k < self._inicio.chave:
            self._inicio = _No(valor, k, self._inicio)
            return 1
        anterior = self._inicio
        passos = 1
        while anterior.proximo is not None and not k < anterior.proximo.chave:
            anterior = anterior.proximo
            passos += 1
        anterior.proximo = _No(valor, k, anterior.proximo)
        if anterior is self._fim:
            self._fim = anterior.proximo
        return passos

    def primeiro(self):
        if self._inicio is None:
            raise IndexError("lista ordenada vazia")
        return self._inicio.valor

    def remover_inicio(self):
        if self._inicio is None:
            raise IndexError("remover de lista ordenada vazia")
        no = self._inicio
        self._inicio = no.proximo
        if self._inicio is None:
            self._fim = None
        self._tamanho -= 1
        return no.valor

    def remover(self, valor) -> bool:
        anterior = None
        atual = self._inicio
        while atual is not None and atual.valor is not valor:
            anterior = atual
            atual = atual.proximo
        if atual is None:
            return False
        if anterior is None:
            self._inicio = atual.proximo
        else:
            anterior.proximo = atual.proximo
        if atual is self._fim:
            self._fim = anterior
        self._tamanho -= 1
        return True

    def para_array(self):
        resultado = [None] * self._tamanho
        i = 0
        for valor in self:
            resultado[i] = valor
            i += 1
        return resultado
