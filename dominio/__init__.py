"""
Modelo de domínio do Origem com as estruturas próprias de estruturas/ (AED).

    from dominio import Loja
    loja = Loja(prioridade=True)

Ver dominio/loja.py (fachada com desfazer), dominio/estoque.py (lista, hash,
árvore, busca binária, lotes por validade) e dominio/pedidos.py (fila).
"""

from dominio.estoque import ErroEstoque, Estoque, Lote, Produto, ProdutoInexistente
from dominio.loja import Loja, Operacao
from dominio.pedidos import ATENDIDO, CANCELADO, PENDENTE, RECUSADO, FilaDePedidos, Pedido

__all__ = [
    "ATENDIDO", "CANCELADO", "PENDENTE", "RECUSADO",
    "ErroEstoque", "Estoque", "FilaDePedidos", "Loja", "Lote", "Operacao", "Pedido", "Produto",
    "ProdutoInexistente",
]
