"""
Algoritmos de ordenação.

Todos recebem um array (ou qualquer iterável) e devolvem um NOVO array
ordenado, sem alterar a entrada. ``chave`` funciona como no sorted() do Python
(ex.: chave=lambda r: r.score) e ``decrescente=True`` inverte a ordem.

| Algoritmo | Melhor     | Médio      | Pior       | Memória extra | Estável |
|-----------|------------|------------|------------|---------------|---------|
| insertion | O(n)       | O(n²)      | O(n²)      | O(1)          | sim     |
| selection | O(n²)      | O(n²)      | O(n²)      | O(1)          | não     |
| merge     | O(n log n) | O(n log n) | O(n log n) | O(n)          | sim     |
| quick     | O(n log n) | O(n log n) | O(n²)      | O(log n)      | não     |
"""

from estruturas.lista import ListaEncadeada


def _copiar(itens):
    # Copia para um array de tamanho fixo sem list()/append(). Passa pela
    # ListaEncadeada para percorrer a entrada uma vez só (funciona com geradores).
    lista = ListaEncadeada()
    for item in itens:
        lista.inserir_fim(item)
    return lista.para_array()


def _comparador(chave, decrescente):
    """Devolve vem_antes(a, b): True se ``a`` deve ficar antes de ``b``."""
    if chave is None:
        chave = _identidade
    if decrescente:
        return lambda a, b: chave(a) > chave(b)
    return lambda a, b: chave(a) < chave(b)


def _identidade(x):
    return x


def insertion_sort(itens, chave=None, decrescente=False):
    """Bom para entradas pequenas ou quase ordenadas (melhor caso O(n))."""
    a = _copiar(itens)
    vem_antes = _comparador(chave, decrescente)
    for i in range(1, len(a)):
        atual = a[i]
        j = i - 1
        while j >= 0 and vem_antes(atual, a[j]):
            a[j + 1] = a[j]
            j -= 1
        a[j + 1] = atual
    return a


def selection_sort(itens, chave=None, decrescente=False):
    """Faz no máximo n-1 trocas, mas sempre O(n²) comparações."""
    a = _copiar(itens)
    vem_antes = _comparador(chave, decrescente)
    n = len(a)
    for i in range(n - 1):
        escolhido = i
        for j in range(i + 1, n):
            if vem_antes(a[j], a[escolhido]):
                escolhido = j
        if escolhido != i:
            a[i], a[escolhido] = a[escolhido], a[i]
    return a


def merge_sort(itens, chave=None, decrescente=False):
    """
    Versão bottom-up (sem recursão): intercala blocos de tamanho 1, 2, 4, ...
    alternando entre dois arrays para não alocar memória a cada nível.
    """
    origem = _copiar(itens)
    n = len(origem)
    vem_antes = _comparador(chave, decrescente)
    destino = [None] * n
    largura = 1
    while largura < n:
        for inicio in range(0, n, 2 * largura):
            meio = min(inicio + largura, n)
            fim = min(inicio + 2 * largura, n)
            i, j, k = inicio, meio, inicio
            while i < meio and j < fim:
                # Só pega da direita se ela vier ESTRITAMENTE antes: em empate
                # fica o da esquerda, o que torna o merge estável.
                if vem_antes(origem[j], origem[i]):
                    destino[k] = origem[j]
                    j += 1
                else:
                    destino[k] = origem[i]
                    i += 1
                k += 1
            while i < meio:
                destino[k] = origem[i]
                i += 1
                k += 1
            while j < fim:
                destino[k] = origem[j]
                j += 1
                k += 1
        origem, destino = destino, origem
        largura *= 2
    return origem


def quick_sort(itens, chave=None, decrescente=False):
    """
    Pivô = mediana de três (início, meio, fim), o que evita o pior caso O(n²)
    em entradas já ordenadas. Recursão só na parte menor e laço na maior, então
    a pilha de chamadas fica em O(log n) mesmo no pior caso.
    """
    a = _copiar(itens)
    vem_antes = _comparador(chave, decrescente)
    _quick(a, 0, len(a) - 1, vem_antes)
    return a


def _quick(a, inicio, fim, vem_antes):
    while inicio < fim:
        if fim - inicio < 16:
            # Partes pequenas: insertion sort é mais rápido que continuar particionando.
            _insertion_intervalo(a, inicio, fim, vem_antes)
            return
        p = _particionar(a, inicio, fim, vem_antes)
        if p - inicio < fim - p:
            _quick(a, inicio, p - 1, vem_antes)
            inicio = p + 1
        else:
            _quick(a, p + 1, fim, vem_antes)
            fim = p - 1


def _particionar(a, inicio, fim, vem_antes):
    meio = (inicio + fim) // 2
    # Ordena a[inicio], a[meio], a[fim] entre si; a mediana fica em a[meio].
    if vem_antes(a[meio], a[inicio]):
        a[inicio], a[meio] = a[meio], a[inicio]
    if vem_antes(a[fim], a[inicio]):
        a[inicio], a[fim] = a[fim], a[inicio]
    if vem_antes(a[fim], a[meio]):
        a[meio], a[fim] = a[fim], a[meio]
    # Lomuto com o pivô (mediana) movido para o fim.
    a[meio], a[fim] = a[fim], a[meio]
    pivo = a[fim]
    i = inicio
    for j in range(inicio, fim):
        if vem_antes(a[j], pivo):
            a[i], a[j] = a[j], a[i]
            i += 1
    a[i], a[fim] = a[fim], a[i]
    return i


def _insertion_intervalo(a, inicio, fim, vem_antes):
    for i in range(inicio + 1, fim + 1):
        atual = a[i]
        j = i - 1
        while j >= inicio and vem_antes(atual, a[j]):
            a[j + 1] = a[j]
            j -= 1
        a[j + 1] = atual
