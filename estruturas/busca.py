"""
Algoritmos de busca. Ambos devolvem o índice do alvo ou -1 se não achar.

| Algoritmo  | Melhor | Pior     | Exige ordenado? |
|------------|--------|----------|-----------------|
| sequencial | O(1)   | O(n)     | não             |
| binária    | O(1)   | O(log n) | sim             |
"""


def busca_sequencial(array, alvo, chave=None):
    for i in range(len(array)):
        valor = array[i] if chave is None else chave(array[i])
        if valor == alvo:
            return i
    return -1


def busca_binaria(array, alvo, chave=None):
    """``array`` precisa estar em ordem crescente de ``chave``. Devolve a primeira ocorrência."""
    inicio, fim = 0, len(array) - 1
    encontrado = -1
    while inicio <= fim:
        meio = (inicio + fim) // 2
        valor = array[meio] if chave is None else chave(array[meio])
        if valor == alvo:
            # Continua pela esquerda para achar a primeira ocorrência, se houver repetidos.
            encontrado = meio
            fim = meio - 1
        elif valor < alvo:
            inicio = meio + 1
        else:
            fim = meio - 1
    return encontrado
