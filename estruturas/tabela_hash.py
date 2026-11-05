"""Tabela hash com encadeamento separado e redimensionamento."""


class _Entrada:
    __slots__ = ("chave", "valor", "proxima")

    def __init__(self, chave, valor, proxima):
        self.chave = chave
        self.valor = valor
        self.proxima = proxima


def _hash(chave) -> int:
    # Função de hash própria para os tipos que usamos como chave (ids e textos).
    # Texto: hash polinomial (base 31, como o String.hashCode do Java), que
    # depende da ordem dos caracteres, então "ab" e "ba" caem em baldes diferentes.
    if isinstance(chave, int):
        return chave
    if isinstance(chave, str):
        h = 0
        for caractere in chave:
            h = (h * 31 + ord(caractere)) & 0xFFFFFFFF
        return h
    if isinstance(chave, tuple):
        h = 17
        for parte in chave:
            h = (h * 31 + _hash(parte)) & 0xFFFFFFFF
        return h
    raise TypeError(f"tipo de chave não suportado: {type(chave).__name__}")


class TabelaHash:
    """
    Complexidade (n = elementos, m = baldes):
    - inserir, obter, remover, contem: O(1) em média, O(n) no pior caso
      (todas as chaves no mesmo balde).
    - Redimensionar: O(n), mas acontece só quando n/m passa de 0.75 e dobra m,
      então o custo amortizado por inserção continua O(1).
    """

    FATOR_CARGA_MAXIMO = 0.75

    def __init__(self, capacidade_inicial: int = 16):
        self._baldes = [None] * capacidade_inicial
        self._capacidade = capacidade_inicial
        self._tamanho = 0

    def __len__(self):
        return self._tamanho

    def __contains__(self, chave):
        return self.contem(chave)

    def __getitem__(self, chave):
        return self.obter(chave)

    def __setitem__(self, chave, valor):
        self.inserir(chave, valor)

    def _indice(self, chave) -> int:
        return _hash(chave) % self._capacidade

    def _procurar(self, chave):
        entrada = self._baldes[self._indice(chave)]
        while entrada is not None:
            if entrada.chave == chave:
                return entrada
            entrada = entrada.proxima
        return None

    def inserir(self, chave, valor):
        """Insere ou atualiza o valor da chave."""
        entrada = self._procurar(chave)
        if entrada is not None:
            entrada.valor = valor
            return
        indice = self._indice(chave)
        self._baldes[indice] = _Entrada(chave, valor, self._baldes[indice])
        self._tamanho += 1
        if self._tamanho / self._capacidade > self.FATOR_CARGA_MAXIMO:
            self._redimensionar(self._capacidade * 2)

    def obter(self, chave, padrao=KeyError):
        entrada = self._procurar(chave)
        if entrada is not None:
            return entrada.valor
        if padrao is KeyError:
            raise KeyError(chave)
        return padrao

    def contem(self, chave) -> bool:
        return self._procurar(chave) is not None

    def remover(self, chave):
        indice = self._indice(chave)
        anterior = None
        entrada = self._baldes[indice]
        while entrada is not None:
            if entrada.chave == chave:
                if anterior is None:
                    self._baldes[indice] = entrada.proxima
                else:
                    anterior.proxima = entrada.proxima
                self._tamanho -= 1
                return entrada.valor
            anterior = entrada
            entrada = entrada.proxima
        raise KeyError(chave)

    def itens(self):
        """Pares (chave, valor) sem ordem definida."""
        for i in range(self._capacidade):
            entrada = self._baldes[i]
            while entrada is not None:
                yield entrada.chave, entrada.valor
                entrada = entrada.proxima

    def chaves(self):
        for chave, _ in self.itens():
            yield chave

    def _redimensionar(self, nova_capacidade: int):
        antigos = self._baldes
        self._baldes = [None] * nova_capacidade
        self._capacidade = nova_capacidade
        for i in range(len(antigos)):
            entrada = antigos[i]
            while entrada is not None:
                proxima = entrada.proxima
                indice = self._indice(entrada.chave)
                entrada.proxima = self._baldes[indice]
                self._baldes[indice] = entrada
                entrada = proxima
