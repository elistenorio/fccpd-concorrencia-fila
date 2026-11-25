"""
Laboratório de AED do painel: as estruturas de ``estruturas/`` rodando de verdade.

Cada operação do navegador chama o método real da classe; o ``retrato`` só lê
os nós/arrays internos para o frontend desenhar exatamente o que está na
memória (por isso acessa atributos com ``_``: é visualização, não uso).

Ordenação e busca são instrumentadas pelo parâmetro ``chave``: o comparador
chama chave(a) e chave(b) a cada comparação, então contar as chamadas conta as
comparações sem mexer no algoritmo.

Mesma regra de AED do resto: estado em estruturas próprias, arrays [None] * n e
classes com __slots__; dicionário só no formato final do JSON (retratos e respostas).
"""

import random
import threading
import time

from estruturas import (
    ArvoreBusca,
    Fila,
    FilaCircular,
    ListaEncadeada,
    Pilha,
    TabelaHash,
    TopK,
    busca_binaria,
    busca_sequencial,
    insertion_sort,
    merge_sort,
    quick_sort,
    selection_sort,
)

CAPACIDADE_ANEL = 8
K_TOP = 5
ALGORITMOS = (
    ("insertion", insertion_sort),
    ("selection", selection_sort),
    ("merge", merge_sort),
    ("quick", quick_sort),
)


class ErroOperacao(Exception):
    pass


def _encadeada(no, campo):
    """Valores de uma cadeia de nós (lista, fila, pilha)."""
    valores = ListaEncadeada()
    while no is not None:
        valores.inserir_fim(no.valor)
        no = getattr(no, campo)
    return valores.para_array()


def _retrato_arvore(raiz, caminho_ate=None):
    """Nós com posição (x = ordem em-ordem, y = profundidade) e o caminho de busca."""
    nos = ListaEncadeada()
    arestas = ListaEncadeada()
    # Em-ordem iterativo com pilha própria; x cresce na ordem das chaves.
    pilha = Pilha()
    atual, x = raiz, 0
    profundidade = TabelaHash()
    if raiz is not None:
        profundidade[id(raiz)] = 0
    while atual is not None or not pilha.vazia():
        while atual is not None:
            pilha.empilhar(atual)
            for filho in (atual.esquerda, atual.direita):
                if filho is not None:
                    profundidade[id(filho)] = profundidade[id(atual)] + 1
                    arestas.inserir_fim((atual.chave, filho.chave))
            atual = atual.esquerda
        atual = pilha.desempilhar()
        nos.inserir_fim({"chave": atual.chave, "x": x, "y": profundidade[id(atual)]})
        x += 1
        atual = atual.direita

    caminho = ListaEncadeada()
    if caminho_ate is not None:
        atual = raiz
        while atual is not None:
            caminho.inserir_fim(atual.chave)
            if atual.chave == caminho_ate:
                break
            atual = atual.esquerda if caminho_ate < atual.chave else atual.direita
    return {"nos": nos.para_array(), "arestas": arestas.para_array(), "caminho": caminho.para_array()}


class Laboratorio:
    def __init__(self):
        self._lock = threading.Lock()  # o servidor atende requisições em threads
        self.reiniciar_tudo()

    def reiniciar_tudo(self):
        self.lista = ListaEncadeada()
        self.pilha = Pilha()
        self.fila = Fila()
        self.anel = FilaCircular(CAPACIDADE_ANEL, sobrescrever=False)
        self.hash = TabelaHash(capacidade_inicial=4)
        self.arvore = ArvoreBusca()
        self.top = TopK(K_TOP)
        self.ultima_busca = None

    def reiniciar(self, estrutura):
        with self._lock:
            if estrutura == "lista":
                self.lista = ListaEncadeada()
            elif estrutura == "pilha":
                self.pilha = Pilha()
            elif estrutura == "fila":
                self.fila = Fila()
            elif estrutura == "anel":
                self.anel = FilaCircular(CAPACIDADE_ANEL, sobrescrever=self.anel._sobrescrever)
            elif estrutura == "hash":
                self.hash = TabelaHash(capacidade_inicial=4)
            elif estrutura == "arvore":
                self.arvore = ArvoreBusca()
                self.ultima_busca = None
            elif estrutura == "top":
                self.top = TopK(K_TOP)
            else:
                raise ErroOperacao(f"estrutura desconhecida: {estrutura}")
            return self._retrato(estrutura)

    def operar(self, estrutura, operacao, valor=None):
        with self._lock:
            inicio = time.perf_counter_ns()
            try:
                resultado = self._executar(estrutura, operacao, valor)
            except (IndexError, KeyError, ValueError) as erro:
                raise ErroOperacao(str(erro) or erro.__class__.__name__) from erro
            except Exception as erro:  # FilaCheiaError e afins
                raise ErroOperacao(f"{erro.__class__.__name__}: {erro}") from erro
            duracao = time.perf_counter_ns() - inicio
            retrato = self._retrato(estrutura)
            retrato["resultado"] = resultado
            retrato["duracao_ns"] = duracao
            return retrato

    def retrato(self, estrutura):
        with self._lock:
            return self._retrato(estrutura)

    def _executar(self, estrutura, op, v):
        if estrutura == "lista":
            if op == "inserir_inicio":
                self.lista.inserir_inicio(v)
            elif op == "inserir_fim":
                self.lista.inserir_fim(v)
            elif op == "remover_inicio":
                return self.lista.remover_inicio()
            elif op == "remover":
                return self.lista.remover(v)
            elif op == "contem":
                return self.lista.contem(v)
            else:
                raise ErroOperacao(f"operação desconhecida: {op}")
        elif estrutura == "pilha":
            if op == "empilhar":
                self.pilha.empilhar(v)
            elif op == "desempilhar":
                return self.pilha.desempilhar()
            else:
                raise ErroOperacao(f"operação desconhecida: {op}")
        elif estrutura == "fila":
            if op == "enfileirar":
                self.fila.enfileirar(v)
            elif op == "desenfileirar":
                return self.fila.desenfileirar()
            else:
                raise ErroOperacao(f"operação desconhecida: {op}")
        elif estrutura == "anel":
            if op == "enfileirar":
                self.anel.enfileirar(v)
            elif op == "desenfileirar":
                return self.anel.desenfileirar()
            elif op == "sobrescrever":
                # Recria com o outro modo, mantendo os itens na ordem.
                novo = FilaCircular(CAPACIDADE_ANEL, sobrescrever=bool(v))
                for item in self.anel:
                    novo.enfileirar(item)
                self.anel = novo
            else:
                raise ErroOperacao(f"operação desconhecida: {op}")
        elif estrutura == "hash":
            if op == "inserir":
                self.hash.inserir(str(v), len(str(v)))
            elif op == "remover":
                return self.hash.remover(str(v))
            elif op == "obter":
                return self.hash.obter(str(v))
            else:
                raise ErroOperacao(f"operação desconhecida: {op}")
        elif estrutura == "arvore":
            chave = int(v) if v is not None else None
            if op == "inserir":
                self.arvore.inserir(chave)
                self.ultima_busca = chave
            elif op == "remover":
                self.ultima_busca = None
                return self.arvore.remover(chave)
            elif op == "buscar":
                self.ultima_busca = chave
                return chave in self.arvore
            elif op == "inserir_ordenado":
                # Demonstração da degeneração: chaves crescentes viram uma "lista".
                self.arvore = ArvoreBusca()
                for c in range(1, int(v) + 1):
                    self.arvore.inserir(c * 10)
                self.ultima_busca = None
            elif op == "inserir_aleatorio":
                self.arvore = ArvoreBusca()
                chaves = [None] * int(v)
                for i in range(int(v)):
                    chaves[i] = (i + 1) * 10
                random.shuffle(chaves)
                for c in chaves:
                    self.arvore.inserir(c)
                self.ultima_busca = None
            else:
                raise ErroOperacao(f"operação desconhecida: {op}")
        elif estrutura == "top":
            if op == "oferecer":
                score = round(random.uniform(0, 100), 1) if v is None else float(v)
                self.top.oferecer(f"item {score}", score)
                return score
            raise ErroOperacao(f"operação desconhecida: {op}")
        else:
            raise ErroOperacao(f"estrutura desconhecida: {estrutura}")
        return None

    def _retrato(self, estrutura):
        if estrutura == "lista":
            return {"valores": _encadeada(self.lista._inicio, "proximo"), "tamanho": len(self.lista)}
        if estrutura == "pilha":
            return {"valores": _encadeada(self.pilha._topo, "abaixo"), "tamanho": len(self.pilha)}
        if estrutura == "fila":
            return {"valores": _encadeada(self.fila._inicio, "proximo"), "tamanho": len(self.fila)}
        if estrutura == "anel":
            return {
                "posicoes": [*self.anel._dados],
                "inicio": self.anel.inicio,
                "tamanho": len(self.anel),
                "capacidade": self.anel.capacidade,
                "sobrescrever": self.anel._sobrescrever,
            }
        if estrutura == "hash":
            baldes = [None] * self.hash.capacidade
            for i in range(self.hash.capacidade):
                cadeia = ListaEncadeada()
                entrada = self.hash._baldes[i]
                while entrada is not None:
                    cadeia.inserir_fim(entrada.chave)
                    entrada = entrada.proxima
                baldes[i] = cadeia.para_array()
            return {
                "baldes": baldes,
                "tamanho": len(self.hash),
                "capacidade": self.hash.capacidade,
                "fator_carga": round(len(self.hash) / self.hash.capacidade, 2),
            }
        if estrutura == "arvore":
            retrato = _retrato_arvore(self.arvore._raiz, self.ultima_busca)
            retrato["tamanho"] = len(self.arvore)
            retrato["altura"] = self.arvore.altura()
            return retrato
        if estrutura == "top":
            return {
                "itens": [{"item": i, "score": s} for i, s in self.top.resultado()],
                "k": K_TOP,
                "limiar": None if self.top.limiar == float("-inf") else self.top.limiar,
            }
        raise ErroOperacao(f"estrutura desconhecida: {estrutura}")


# ------------------------------------------------------------- ordenação e busca

def _gerar_entrada(n, tipo, semente):
    rng = random.Random(semente)
    a = [None] * n
    for i in range(n):
        a[i] = i
    if tipo == "ordenada":
        return a
    if tipo == "invertida":
        for i in range(n // 2):
            a[i], a[n - 1 - i] = a[n - 1 - i], a[i]
        return a
    rng.shuffle(a)
    if tipo == "quase":
        a = insertion_sort(a)
        for _ in range(max(1, n // 20)):  # 5% de trocas
            i, j = rng.randrange(n), rng.randrange(n)
            a[i], a[j] = a[j], a[i]
    return a


class Medida:
    __slots__ = ("comparacoes", "ms", "correto")

    def __init__(self, comparacoes, ms, correto):
        self.comparacoes = comparacoes
        self.ms = ms
        self.correto = correto


def _medir(algoritmo, entrada):
    chamadas = [0]

    def chave(x):
        chamadas[0] += 1
        return x

    inicio = time.perf_counter()
    saida = algoritmo(entrada, chave=chave)
    duracao = time.perf_counter() - inicio
    correto = True
    for i in range(1, len(saida)):
        if saida[i - 1] > saida[i]:
            correto = False
            break
    # O comparador chama chave() nos dois lados de cada comparação.
    return Medida(chamadas[0] // 2, round(duracao * 1000, 2), correto)


TAMANHOS_CURVA = (50, 100, 200, 400, 600, 800, 1000)


def corrida_ordenacao(n, tipo, semente=42):
    entrada = _gerar_entrada(n, tipo, semente)
    # medidas[a] = Medida do algoritmo a na entrada pedida; curvas[a][i] = comparações com TAMANHOS_CURVA[i]
    medidas = [None] * len(ALGORITMOS)
    curvas = [None] * len(ALGORITMOS)
    for a in range(len(ALGORITMOS)):
        algoritmo = ALGORITMOS[a][1]
        medidas[a] = _medir(algoritmo, entrada)
        pontos = [None] * len(TAMANHOS_CURVA)
        for i in range(len(TAMANHOS_CURVA)):
            pontos[i] = _medir(algoritmo, _gerar_entrada(TAMANHOS_CURVA[i], tipo, semente)).comparacoes
        curvas[a] = pontos
    amostra = [None] * min(n, 60)
    for i in range(len(amostra)):
        amostra[i] = entrada[i]
    # Daqui para baixo é só o formato do JSON da resposta.
    nomes = [nome for nome, _ in ALGORITMOS]
    return {
        "n": n,
        "tipo": tipo,
        "resultados": {nomes[a]: {"comparacoes": m.comparacoes, "ms": m.ms, "correto": m.correto} for a, m in enumerate(medidas)},
        "tamanhos": [*TAMANHOS_CURVA],
        "curvas": {nomes[a]: curvas[a] for a in range(len(nomes))},
        "amostra": amostra,
    }


def _rastrear(busca, valores, alvo):
    """(índice encontrado, array dos índices visitados) de uma busca sobre ``valores``."""
    n = len(valores)
    visitados = ListaEncadeada()
    # Os elementos são índices; a chave registra a visita e devolve o valor.
    indices = [None] * n
    for i in range(n):
        indices[i] = i

    def chave(i):
        visitados.inserir_fim(i)
        return valores[i]

    encontrado = busca(indices, alvo, chave=chave)
    return encontrado, visitados.para_array()


def comparar_buscas(n, alvo=None, semente=7):
    """Array ordenado com valores crescentes (saltos aleatórios) e os índices visitados por cada busca."""
    rng = random.Random(semente)
    valores = [None] * n
    atual = 0
    for i in range(n):
        atual += rng.randint(1, 4)
        valores[i] = atual
    if alvo is None:
        alvo = valores[rng.randrange(n)]

    seq_indice, seq_visitados = _rastrear(busca_sequencial, valores, alvo)
    bin_indice, bin_visitados = _rastrear(busca_binaria, valores, alvo)
    return {
        "valores": valores,
        "alvo": alvo,
        "sequencial": {"indice": seq_indice, "visitados": seq_visitados},
        "binaria": {"indice": bin_indice, "visitados": bin_visitados},
    }
