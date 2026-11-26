"""
Pedidos (requisições de saída de estoque) e a fila em que esperam atendimento.

    FilaDePedidos(prioridade=False)   Fila (FIFO): atende por ordem de chegada
    FilaDePedidos(prioridade=True)    ListaOrdenada por (-prioridade, chegada):
                                      maior prioridade primeiro; empate = ordem de chegada

Dois detalhes para o "desfazer" da Loja:
- cancelar(pedido) é remoção preguiçosa: marca o pedido e proximo() pula os
  cancelados. Tirar do meio de uma fila encadeada seria O(n); assim é O(1).
- devolver(pedido) põe de volta na frente um pedido já atendido. Na FIFO isso
  usa uma Pilha de devolvidos, consultada antes da fila: como o desfazer é
  LIFO, vários pedidos devolvidos saem de novo na ordem original.
"""

from estruturas import Fila, ListaOrdenada, Pilha

PENDENTE = "pendente"
ATENDIDO = "atendido"
RECUSADO = "recusado"
CANCELADO = "cancelado"


class Pedido:
    __slots__ = ("id", "codigo", "quantidade", "prioridade", "cliente", "status", "seq", "motivo")

    def __init__(self, id_, codigo, quantidade, prioridade=0, cliente=None):
        if quantidade <= 0:
            raise ValueError("quantidade do pedido precisa ser positiva")
        self.id = id_
        self.codigo = codigo
        self.quantidade = quantidade
        self.prioridade = prioridade
        self.cliente = cliente
        self.status = PENDENTE
        self.seq = 0  # ordem de chegada, dada pela fila
        self.motivo = None

    def __repr__(self):
        return f"Pedido({self.id}, {self.codigo!r} x{self.quantidade}, p={self.prioridade}, {self.status})"


class FilaDePedidos:
    def __init__(self, prioridade=False):
        self.prioridade = prioridade
        self._fila = ListaOrdenada(chave=lambda p: (-p.prioridade, p.seq)) if prioridade else Fila()
        self._devolvidos = Pilha()
        self._seq = 0
        self._pendentes = 0

    def __len__(self):
        """Pedidos pendentes (sem contar os cancelados que ainda estão na estrutura)."""
        return self._pendentes

    def entrar(self, pedido):
        """FIFO: O(1). Prioridade: O(n) no pior caso (inserção ordenada)."""
        self._seq += 1
        pedido.seq = self._seq
        pedido.status = PENDENTE
        self._guardar(pedido)
        self._pendentes += 1

    def _guardar(self, pedido):
        if self.prioridade:
            self._fila.inserir(pedido)
        else:
            self._fila.enfileirar(pedido)

    def proximo(self):
        """Tira o próximo pendente (None se não houver). O(1) amortizado."""
        while not self._devolvidos.vazia():
            pedido = self._devolvidos.desempilhar()
            if pedido.status == PENDENTE:
                self._pendentes -= 1
                return pedido
        while not self._fila.vazia():
            pedido = self._fila.remover_inicio() if self.prioridade else self._fila.desenfileirar()
            if pedido.status == PENDENTE:
                self._pendentes -= 1
                return pedido
        return None

    def espiar(self):
        """Próximo pendente sem tirar (para mostrar na tela)."""
        for pedido in self:
            return pedido
        return None

    def cancelar(self, pedido):
        """Remoção preguiçosa: O(1)."""
        if pedido.status != PENDENTE:
            raise ValueError(f"pedido {pedido.id} não está pendente ({pedido.status})")
        pedido.status = CANCELADO
        self._pendentes -= 1

    def reativar(self, pedido):
        """Desfaz cancelar(): o pedido ainda está na estrutura, só volta a valer."""
        pedido.status = PENDENTE
        self._pendentes += 1

    def devolver(self, pedido):
        """Desfaz um atendimento: o pedido volta para onde estava."""
        pedido.status = PENDENTE
        pedido.motivo = None
        self._pendentes += 1
        if self.prioridade:
            self._fila.inserir(pedido)  # mesma chave (-prioridade, seq) = mesma posição
        else:
            self._devolvidos.empilhar(pedido)

    def __iter__(self):
        """Pendentes na ordem em que serão atendidos (sem alterar a fila)."""
        # A Pilha só deixa ver o topo: desempilha tudo anotando a ordem (topo
        # primeiro = ordem de saída) e empilha de volta pela pilha auxiliar.
        ordem = Fila()
        aux = Pilha()
        while not self._devolvidos.vazia():
            p = self._devolvidos.desempilhar()
            ordem.enfileirar(p)
            aux.empilhar(p)
        while not aux.vazia():
            self._devolvidos.empilhar(aux.desempilhar())
        for p in ordem:
            if p.status == PENDENTE:
                yield p
        for p in self._fila:
            if p.status == PENDENTE:
                yield p
