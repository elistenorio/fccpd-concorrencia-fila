from pydantic import BaseModel


class PedidoCreate(BaseModel):
    produto_id: int


class PedidoResponse(BaseModel):
    pedido_id: int
    produto_id: int
    mensagem: str
