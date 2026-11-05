from pydantic import BaseModel


class PedidoCreate(BaseModel):
    produto_id: int
    cliente_id: int | None = None


class PedidoResponse(BaseModel):
    pedido_id: int
    produto_id: int
    mensagem: str
    status: str


class PedidoStatusResponse(BaseModel):
    pedido_id: int
    status: str


class RecomendacaoResponse(BaseModel):
    produto_id: int
    nome: str
    score: float


class ProdutoResponse(BaseModel):
    id: int
    nome: str
    estoque: int
    categoria: str | None
    artesao: str | None
    # False quando o serviço de recomendações não respondeu: a página do
    # produto continua funcionando, só sem a seção de recomendações.
    recomendacoes_disponiveis: bool
    recomendacoes: list[RecomendacaoResponse]
