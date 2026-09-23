import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Produto, Pedido
from app.queue import tentar_publicar_pedido
from app.schemas import PedidoCreate, PedidoResponse, PedidoStatusResponse

Base.metadata.create_all(bind=engine)

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

    pedido = Pedido(produto_id=produto.id)
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


@app.get("/pedidos/{pedido_id}", response_model=PedidoStatusResponse)
def consultar_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return PedidoStatusResponse(pedido_id=pedido.id, status=pedido.status)
