"""Os K maiores de uma sequência, sem ordenar a sequência inteira."""


class TopK:
    """
    Guarda os k itens de maior score num array fixo, em ordem decrescente.

    Cada oferta é um passo de insertion sort limitado a k posições: se o score
    não supera o menor guardado (``limiar``), é descartado em O(1); senão,
    desloca os menores uma posição e entra no lugar certo, em O(k).

    Para n candidatos: O(n·k) no pior caso e ~O(n) na prática (quase todos são
    descartados), contra O(n log n) de ordenar tudo para depois cortar em k.
    Memória O(k). Empates não substituem: quem chegou antes fica na frente.
    """

    def __init__(self, k: int):
        if k < 1:
            raise ValueError("k deve ser >= 1")
        self._k = k
        self._itens = [None] * k
        self._scores = [0.0] * k
        self._tamanho = 0
        self.limiar = float("-inf")

    def __len__(self):
        return self._tamanho

    def oferecer(self, item, score):
        if score <= self.limiar:
            return
        # Começa da última posição ocupada (ou da primeira livre) e desloca
        # para a direita quem tiver score menor; o último sai se estiver cheio.
        i = self._tamanho if self._tamanho < self._k else self._k - 1
        while i > 0 and self._scores[i - 1] < score:
            if i < self._k:
                self._itens[i] = self._itens[i - 1]
                self._scores[i] = self._scores[i - 1]
            i -= 1
        self._itens[i] = item
        self._scores[i] = score
        if self._tamanho < self._k:
            self._tamanho += 1
        if self._tamanho == self._k:
            self.limiar = self._scores[self._k - 1]

    def resultado(self):
        """Array de pares (item, score), do maior para o menor score."""
        pares = [None] * self._tamanho
        for i in range(self._tamanho):
            pares[i] = (self._itens[i], self._scores[i])
        return pares
