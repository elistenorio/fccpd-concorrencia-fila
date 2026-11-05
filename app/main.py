import logging
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends, Query, Response
from sqlalchemy.orm import Session
from app.database import engine, get_db
from app.models import Produto, Pedido, criar_e_migrar
from app.queue import tentar_publicar_pedido
from app.recomendacoes_cliente import buscar_recomendacoes
from app.schemas import (
    PedidoCreate,
    PedidoResponse,
    PedidoStatusResponse,
    ProdutoResponse,
)

criar_e_migrar(engine)

app = FastAPI()
logger = logging.getLogger("uvicorn.error")

@app.get("/")
def health():
    return {"status": "ok"}


def publicar_na_fila(pedido_id: int):
    if not tentar_publicar_pedido(pedido_id):
        logger.warning(
            "Fila indisponível: pedido %s fica pendente até o reconciliador publicá-lo",
            pedido_id,
        )


@app.post("/pedidos", response_model=PedidoResponse, status_code=201)
def criar_pedido(
    pedido_in: PedidoCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):

    # O with_for_update() aplica um lock pessimista na linha do produto.
    # Isso impede que duas transações simultâneas leiam o mesmo estoque
    # e tentem decrementá-lo ao mesmo tempo (evitando race conditions).

    produto = (
        db.query(Produto)
        .filter(Produto.id == pedido_in.produto_id)
        .with_for_update()
        .first()
    )

    if produto is None:
        db.rollback() # Libera o lock se o produto não existir
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    if produto.estoque < 1:
        db.rollback() # Libera o lock se não houver estoque
        raise HTTPException(status_code=409, detail="Produto sem estoque")

    produto.estoque -= 1

    pedido = Pedido(produto_id=produto.id, cliente_id=pedido_in.cliente_id)
    db.add(pedido)
    db.commit() # Libera o lock e efetiva a compra
    db.refresh(pedido)

    # A compra já está efetivada no banco, então a fila não pode atrasá-la nem
    # derrubá-la: a publicação roda depois que a resposta é enviada. Se o Redis
    # estiver fora, o pedido fica "pendente" e o reconciliador o publica quando
    # a fila voltar (o banco é a fonte da verdade, nenhum pedido se perde).
    background_tasks.add_task(publicar_na_fila, pedido.id)

    return PedidoResponse(
        pedido_id=pedido.id,
        produto_id=produto.id,
        mensagem="Pedido criado com sucesso",
        status=pedido.status,
    )


@app.get("/produtos/{produto_id}", response_model=ProdutoResponse)
def consultar_produto(
    produto_id: int,
    response: Response,
    limite: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    produto = db.get(Produto, produto_id)
    if produto is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    # Id curto que viaja no header X-Request-ID e aparece no log dos dois
    # serviços, ligando a chamada da api à resposta das recomendações.
    request_id = uuid.uuid4().hex[:8]
    response.headers["X-Request-ID"] = request_id
    recomendacoes = buscar_recomendacoes(produto_id, limite, request_id)

    return ProdutoResponse(
        id=produto.id,
        nome=produto.nome,
        estoque=produto.estoque,
        categoria=produto.categoria,
        artesao=produto.artesao,
        recomendacoes_disponiveis=recomendacoes is not None,
        recomendacoes=recomendacoes or [],
    )


@app.get("/pedidos/{pedido_id}", response_model=PedidoStatusResponse)
def consultar_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return PedidoStatusResponse(pedido_id=pedido.id, status=pedido.status)
