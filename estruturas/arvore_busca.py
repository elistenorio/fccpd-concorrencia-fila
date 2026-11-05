"""Árvore binária de busca (sem balanceamento)."""

from estruturas.pilha import Pilha


class _No:
    __slots__ = ("chave", "valor", "esquerda", "direita")

    def __init__(self, chave, valor):
        self.chave = chave
        self.valor = valor
        self.esquerda = None
        self.direita = None


class ArvoreBusca:
    """
    Complexidade (h = altura):
    - inserir, buscar, remover, minimo, maximo: O(h)
    - h ~ log n com chaves em ordem aleatória, mas h = n se as chaves chegarem
      já ordenadas (a árvore degenera em lista). Ver test_arvore_degenera.
    - em_ordem: O(n), devolve as chaves ordenadas.

    As operações são iterativas para não estourar o limite de recursão do
    Python no caso degenerado.
    """

    def __init__(self):
        self._raiz = None
        self._tamanho = 0

    def __len__(self):
        return self._tamanho

    def __contains__(self, chave):
        return self._buscar_no(chave) is not None

    def vazia(self) -> bool:
        return self._raiz is None

    def inserir(self, chave, valor=None):
        """Insere ou atualiza o valor da chave."""
        if self._raiz is None:
            self._raiz = _No(chave, valor)
            self._tamanho += 1
            return
        atual = self._raiz
        while True:
            if chave == atual.chave:
                atual.valor = valor
                return
            if chave < atual.chave:
                if atual.esquerda is None:
                    atual.esquerda = _No(chave, valor)
                    break
                atual = atual.esquerda
            else:
                if atual.direita is None:
                    atual.direita = _No(chave, valor)
                    break
                atual = atual.direita
        self._tamanho += 1

    def _buscar_no(self, chave):
        atual = self._raiz
        while atual is not None and atual.chave != chave:
            atual = atual.esquerda if chave < atual.chave else atual.direita
        return atual

    def buscar(self, chave, padrao=None):
        no = self._buscar_no(chave)
        return padrao if no is None else no.valor

    def remover(self, chave) -> bool:
        pai = None
        atual = self._raiz
        while atual is not None and atual.chave != chave:
            pai = atual
            atual = atual.esquerda if chave < atual.chave else atual.direita
        if atual is None:
            return False

        # Dois filhos: copia o sucessor (menor da subárvore direita) para cá e
        # passa a remover o sucessor, que tem no máximo um filho.
        if atual.esquerda is not None and atual.direita is not None:
            pai_sucessor = atual
            sucessor = atual.direita
            while sucessor.esquerda is not None:
                pai_sucessor = sucessor
                sucessor = sucessor.esquerda
            atual.chave, atual.valor = sucessor.chave, sucessor.valor
            pai, atual = pai_sucessor, sucessor

        filho = atual.esquerda if atual.esquerda is not None else atual.direita
        if pai is None:
            self._raiz = filho
        elif pai.esquerda is atual:
            pai.esquerda = filho
        else:
            pai.direita = filho
        self._tamanho -= 1
        return True

    def minimo(self):
        if self._raiz is None:
            raise ValueError("árvore vazia")
        atual = self._raiz
        while atual.esquerda is not None:
            atual = atual.esquerda
        return atual.chave

    def maximo(self):
        if self._raiz is None:
            raise ValueError("árvore vazia")
        atual = self._raiz
        while atual.direita is not None:
            atual = atual.direita
        return atual.chave

    def em_ordem(self):
        """Pares (chave, valor) em ordem crescente de chave, usando a Pilha própria."""
        pilha = Pilha()
        atual = self._raiz
        while atual is not None or not pilha.vazia():
            while atual is not None:
                pilha.empilhar(atual)
                atual = atual.esquerda
            atual = pilha.desempilhar()
            yield atual.chave, atual.valor
            atual = atual.direita

    def altura(self) -> int:
        """Número de níveis (árvore vazia = 0). Percorre por níveis com uma pilha."""
        if self._raiz is None:
            return 0
        maior = 0
        pilha = Pilha()
        pilha.empilhar((self._raiz, 1))
        while not pilha.vazia():
            no, nivel = pilha.desempilhar()
            if nivel > maior:
                maior = nivel
            if no.esquerda is not None:
                pilha.empilhar((no.esquerda, nivel + 1))
            if no.direita is not None:
                pilha.empilhar((no.direita, nivel + 1))
        return maior
