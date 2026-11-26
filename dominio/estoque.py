"""
Estoque do Origem: produtos artesanais, cada um com lotes de validade.

    Estoque
    ├── _produtos   ListaEncadeada de Produto     o "estoque em lista" (cadastro, varredura)
    ├── _indice     TabelaHash  codigo -> Produto  consulta pelo código em O(1) médio
    ├── _por_nome   ArvoreBusca (nome, codigo) -> Produto
    │                                              busca por nome e por prefixo em O(h + k)
    └── _por_preco  array ordenado por preço (merge_sort, refeito só quando o
                    cadastro muda)                 faixa de preço por busca binária O(log n + k)

    Produto.lotes   ListaOrdenada de Lote por validade (inserção ordenada):
                    a saída tira primeiro o lote que vence antes (FEFO,
                    first-expired-first-out); lote sem validade fica no fim.

Preço em centavos (int) para não ter erro de ponto flutuante; validade é
datetime.date ou None (não perecível).
"""

from datetime import date

from estruturas import ArvoreBusca, ListaEncadeada, ListaOrdenada, TabelaHash, limite_inferior, merge_sort

SEM_VALIDADE = date.max  # não perecível: vai para o fim da fila de saída


class ErroEstoque(Exception):
    pass


class ProdutoInexistente(ErroEstoque):
    pass


class Lote:
    __slots__ = ("quantidade", "validade")

    def __init__(self, quantidade, validade=None):
        if quantidade <= 0:
            raise ErroEstoque("quantidade do lote precisa ser positiva")
        self.quantidade = quantidade
        self.validade = validade

    def chave_saida(self):
        return self.validade or SEM_VALIDADE

    def __repr__(self):
        return f"Lote({self.quantidade}, {self.validade})"


class Produto:
    __slots__ = ("codigo", "nome", "categoria", "artesao", "preco_centavos", "lotes")

    def __init__(self, codigo, nome, categoria="", artesao="", preco_centavos=0):
        if preco_centavos < 0:
            raise ErroEstoque("preço negativo")
        self.codigo = codigo
        self.nome = nome
        self.categoria = categoria
        self.artesao = artesao
        self.preco_centavos = preco_centavos
        self.lotes = ListaOrdenada(chave=Lote.chave_saida)

    @property
    def quantidade(self):
        total = 0
        for lote in self.lotes:
            total += lote.quantidade
        return total

    def disponivel(self, hoje=None):
        """Quantidade que pode sair: ignora lotes vencidos antes de ``hoje``."""
        total = 0
        for lote in self.lotes:
            if hoje is None or lote.chave_saida() >= hoje:
                total += lote.quantidade
        return total

    def chave_nome(self):
        # (nome, código) deixa nomes repetidos coexistirem; código como texto para
        # comparar com os limites "" e "￿" das buscas por intervalo.
        return (self.nome.lower(), str(self.codigo))

    def __repr__(self):
        return f"Produto({self.codigo!r}, {self.nome!r}, {self.quantidade} un.)"


class Retirada:
    """O que uma saída tirou de um lote, para o desfazer devolver."""

    __slots__ = ("lote", "quantidade", "esvaziou")

    def __init__(self, lote, quantidade, esvaziou):
        self.lote = lote
        self.quantidade = quantidade
        self.esvaziou = esvaziou


class Estoque:
    def __init__(self):
        self._produtos = ListaEncadeada()
        self._indice = TabelaHash()
        self._por_nome = ArvoreBusca()
        self._por_preco = None  # cache; None = precisa refazer

    def __len__(self):
        return len(self._produtos)

    def __iter__(self):
        return iter(self._produtos)

    # ------------------------------------------------------------ cadastro

    def cadastrar(self, produto):
        """O(1) médio na hash + O(h) na árvore + O(1) na lista."""
        if produto.codigo in self._indice:
            raise ErroEstoque(f"código {produto.codigo} já cadastrado")
        self._produtos.inserir_fim(produto)
        self._indice[produto.codigo] = produto
        self._por_nome.inserir(produto.chave_nome(), produto)
        self._por_preco = None
        return produto

    def remover(self, codigo):
        """O(n): achar o nó na lista encadeada é linear (a hash e a árvore são rápidas)."""
        produto = self.consultar(codigo)
        self._produtos.remover(produto)
        self._indice.remover(codigo)
        self._por_nome.remover(produto.chave_nome())
        self._por_preco = None
        return produto

    def alterar_preco(self, codigo, preco_centavos):
        if preco_centavos < 0:
            raise ErroEstoque("preço negativo")
        produto = self.consultar(codigo)
        anterior = produto.preco_centavos
        produto.preco_centavos = preco_centavos
        self._por_preco = None
        return anterior

    # ------------------------------------------------------------ consultas

    def consultar(self, codigo):
        """Índice por hash: O(1) médio."""
        produto = self._indice.obter(codigo, None)
        if produto is None:
            raise ProdutoInexistente(f"produto {codigo} não existe")
        return produto

    def existe(self, codigo):
        return codigo in self._indice

    def consultar_sequencial(self, codigo):
        """Mesma consulta varrendo a lista: O(n). Só existe para comparar com o índice."""
        for produto in self._produtos:
            if produto.codigo == codigo:
                return produto
        raise ProdutoInexistente(f"produto {codigo} não existe")

    def buscar_nome(self, nome):
        """Produtos com esse nome exato (sem diferenciar maiúsculas). Árvore: O(h + k)."""
        nome = nome.lower()
        return self._coletar(self._por_nome.intervalo((nome, ""), (nome, "￿")))

    def buscar_prefixo(self, prefixo):
        """Nomes que começam com ``prefixo``: todas as chaves entre prefixo e prefixo + '\\uffff'."""
        prefixo = prefixo.lower()
        return self._coletar(self._por_nome.intervalo((prefixo, ""), (prefixo + "￿", "")))

    def faixa_de_preco(self, minimo_centavos, maximo_centavos):
        """Produtos com minimo <= preço <= maximo, do mais barato ao mais caro.

        Busca binária (limite_inferior) no catálogo ordenado por preço: O(log n + k).
        O catálogo é refeito com merge_sort (O(n log n)) só depois de cadastro,
        remoção ou mudança de preço; consultas seguidas reaproveitam.
        """
        catalogo = self._catalogo_por_preco()
        inicio = limite_inferior(catalogo, minimo_centavos, chave=lambda p: p.preco_centavos)
        fim = limite_inferior(catalogo, maximo_centavos + 1, chave=lambda p: p.preco_centavos)
        resultado = [None] * (fim - inicio)
        for i in range(inicio, fim):
            resultado[i - inicio] = catalogo[i]
        return resultado

    def _catalogo_por_preco(self):
        if self._por_preco is None:
            # Estável: preços iguais ficam na ordem de código.
            por_codigo = merge_sort(self._produtos, chave=lambda p: p.codigo)
            self._por_preco = merge_sort(por_codigo, chave=lambda p: p.preco_centavos)
        return self._por_preco

    @staticmethod
    def _coletar(pares):
        lista = ListaEncadeada()
        for _, produto in pares:
            lista.inserir_fim(produto)
        return lista.para_array()

    # ------------------------------------------------------------ movimentação

    def entrada(self, codigo, quantidade, validade=None):
        """Novo lote, inserido em ordem de validade: O(L), L = lotes do produto."""
        lote = Lote(quantidade, validade)
        self.consultar(codigo).lotes.inserir(lote)
        return lote

    def saida(self, codigo, quantidade, hoje=None):
        """Tira ``quantidade`` começando pelo lote que vence antes (FEFO).

        Lotes já vencidos em ``hoje`` não saem (ficam para descarte). Se não
        houver o bastante, nada é tirado (tudo ou nada). Devolve as retiradas,
        para o histórico poder desfazer. O(L).
        """
        if quantidade <= 0:
            raise ErroEstoque("quantidade da saída precisa ser positiva")
        produto = self.consultar(codigo)
        if produto.disponivel(hoje) < quantidade:
            raise ErroEstoque(f"estoque insuficiente de {codigo}: pedido {quantidade}, "
                              f"disponível {produto.disponivel(hoje)}")
        retiradas = ListaEncadeada()
        falta = quantidade
        for lote in produto.lotes.para_array():
            if falta == 0:
                break
            if hoje is not None and lote.chave_saida() < hoje:
                continue  # vencido
            tirar = lote.quantidade if lote.quantidade < falta else falta
            lote.quantidade -= tirar
            falta -= tirar
            esvaziou = lote.quantidade == 0
            if esvaziou:
                produto.lotes.remover(lote)
            retiradas.inserir_fim(Retirada(lote, tirar, esvaziou))
        return retiradas.para_array()

    def devolver(self, codigo, retiradas):
        """Inverso exato de saida(): devolve cada quantidade ao seu lote."""
        produto = self.consultar(codigo)
        for r in retiradas:
            r.lote.quantidade += r.quantidade
            if r.esvaziou:
                produto.lotes.inserir(r.lote)

    def remover_lote(self, codigo, lote):
        self.consultar(codigo).lotes.remover(lote)

    def vencidos(self, hoje):
        """(produto, lote) com validade antes de ``hoje``: só olha o começo de cada lista de lotes."""
        resultado = ListaEncadeada()
        for produto in self._produtos:
            for lote in produto.lotes:
                if lote.chave_saida() >= hoje:
                    break  # ordenada: os próximos vencem depois
                resultado.inserir_fim((produto, lote))
        return resultado.para_array()
