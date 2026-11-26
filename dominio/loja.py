"""
Loja: junta Estoque + FilaDePedidos + histórico de operações (Pilha) com desfazer.

Esta é a fachada que a aplicação usa (ver README, "Modelo de domínio"). Toda
operação que muda o estado passa por aqui e empilha uma Operacao com o que é
preciso para revertê-la; desfazer() desempilha a última e aplica o inverso.

    loja = Loja(prioridade=True)
    loja.cadastrar("VAS-01", "Vaso de barro", categoria="cerâmica", preco_centavos=8900)
    loja.entrada("VAS-01", 10, validade=None)
    pedido = loja.registrar_pedido("VAS-01", 3, prioridade=2)
    loja.processar_proximo()          # atende o pedido de maior prioridade (FEFO no estoque)
    loja.desfazer()                   # volta o estoque e recoloca o pedido na fila

| Operação           | Inverso aplicado pelo desfazer()                  |
|--------------------|---------------------------------------------------|
| cadastrar          | remove o produto                                  |
| remover_produto    | recadastra o mesmo objeto (com os lotes)          |
| alterar_preco      | volta o preço anterior                            |
| entrada            | remove o lote criado                              |
| saida              | devolve cada quantidade ao seu lote               |
| registrar_pedido   | cancela o pedido                                  |
| cancelar_pedido    | reativa o pedido                                  |
| processar_proximo  | devolve o estoque (se atendeu) e recoloca o pedido na fila |
"""

from dominio.estoque import ErroEstoque, Estoque, Produto, ProdutoInexistente
from dominio.pedidos import ATENDIDO, RECUSADO, FilaDePedidos, Pedido
from estruturas import Pilha, TabelaHash


class Operacao:
    __slots__ = ("tipo", "dados", "descricao")

    def __init__(self, tipo, dados, descricao):
        self.tipo = tipo
        self.dados = dados  # tupla com o necessário para o inverso
        self.descricao = descricao

    def __repr__(self):
        return f"Operacao({self.tipo}: {self.descricao})"


class Loja:
    def __init__(self, prioridade=False):
        self.estoque = Estoque()
        self.pedidos = FilaDePedidos(prioridade=prioridade)
        self.historico = Pilha()
        self._pedidos = TabelaHash()  # id -> Pedido (todos, de qualquer status)
        self._proximo_id = 1

    # ------------------------------------------------------------ estoque

    def cadastrar(self, codigo, nome, categoria="", artesao="", preco_centavos=0):
        produto = self.estoque.cadastrar(Produto(codigo, nome, categoria, artesao, preco_centavos))
        self._registrar("cadastrar", (produto,), f"cadastrou {codigo}")
        return produto

    def remover_produto(self, codigo):
        produto = self.estoque.remover(codigo)
        self._registrar("remover_produto", (produto,), f"removeu {codigo}")
        return produto

    def alterar_preco(self, codigo, preco_centavos):
        anterior = self.estoque.alterar_preco(codigo, preco_centavos)
        self._registrar("alterar_preco", (codigo, anterior), f"preço de {codigo}: {anterior} -> {preco_centavos}")

    def entrada(self, codigo, quantidade, validade=None):
        lote = self.estoque.entrada(codigo, quantidade, validade)
        self._registrar("entrada", (codigo, lote), f"entrada de {quantidade} em {codigo}")
        return lote

    def saida(self, codigo, quantidade, hoje=None):
        retiradas = self.estoque.saida(codigo, quantidade, hoje)
        self._registrar("saida", (codigo, retiradas), f"saída de {quantidade} de {codigo}")
        return retiradas

    # ------------------------------------------------------------ pedidos

    def registrar_pedido(self, codigo, quantidade, prioridade=0, cliente=None):
        if not self.estoque.existe(codigo):
            raise ProdutoInexistente(f"produto {codigo} não existe")
        pedido = Pedido(self._proximo_id, codigo, quantidade, prioridade, cliente)
        self._proximo_id += 1
        self._pedidos[pedido.id] = pedido
        self.pedidos.entrar(pedido)
        self._registrar("registrar_pedido", (pedido,), f"pedido {pedido.id}: {quantidade} de {codigo}")
        return pedido

    def pedido(self, id_):
        """Pedido pelo id (hash, O(1) médio)."""
        pedido = self._pedidos.obter(id_, None)
        if pedido is None:
            raise ErroEstoque(f"pedido {id_} não existe")
        return pedido

    def cancelar_pedido(self, pedido):
        self.pedidos.cancelar(pedido)
        self._registrar("cancelar_pedido", (pedido,), f"cancelou pedido {pedido.id}")

    def processar_proximo(self, hoje=None):
        """Atende o próximo pedido da fila: dá saída FEFO ou recusa por falta de estoque."""
        pedido = self.pedidos.proximo()
        if pedido is None:
            return None
        try:
            retiradas = self.estoque.saida(pedido.codigo, pedido.quantidade, hoje)
            pedido.status = ATENDIDO
        except ErroEstoque as erro:  # sem estoque ou produto removido
            retiradas = None
            pedido.status = RECUSADO
            pedido.motivo = str(erro)
        self._registrar("processar", (pedido, retiradas), f"pedido {pedido.id} {pedido.status}")
        return pedido

    def processar_todos(self, hoje=None):
        atendidos = recusados = 0
        while True:
            pedido = self.processar_proximo(hoje)
            if pedido is None:
                return atendidos, recusados
            if pedido.status == ATENDIDO:
                atendidos += 1
            else:
                recusados += 1

    # ------------------------------------------------------------ histórico

    def _registrar(self, tipo, dados, descricao):
        self.historico.empilhar(Operacao(tipo, dados, descricao))

    def desfazer(self):
        """Reverte a última operação (O(1) na pilha + o custo do inverso). Devolve a Operacao."""
        if self.historico.vazia():
            raise ErroEstoque("nada para desfazer")
        op = self.historico.desempilhar()
        d = op.dados
        if op.tipo == "cadastrar":
            self.estoque.remover(d[0].codigo)
        elif op.tipo == "remover_produto":
            self.estoque.cadastrar(d[0])
        elif op.tipo == "alterar_preco":
            self.estoque.alterar_preco(d[0], d[1])
        elif op.tipo == "entrada":
            self.estoque.remover_lote(d[0], d[1])
        elif op.tipo == "saida":
            self.estoque.devolver(d[0], d[1])
        elif op.tipo == "registrar_pedido":
            self.pedidos.cancelar(d[0])
        elif op.tipo == "cancelar_pedido":
            self.pedidos.reativar(d[0])
        elif op.tipo == "processar":
            pedido, retiradas = d
            if retiradas is not None:
                self.estoque.devolver(pedido.codigo, retiradas)
            self.pedidos.devolver(pedido)
        return op

    def ultimas_operacoes(self, n=10):
        """As n operações mais recentes (topo primeiro), sem mexer na pilha."""
        aux = Pilha()
        resultado = [None] * min(n, len(self.historico))
        for i in range(len(resultado)):
            op = self.historico.desempilhar()
            resultado[i] = op
            aux.empilhar(op)
        while not aux.vazia():
            self.historico.empilhar(aux.desempilhar())
        return resultado
