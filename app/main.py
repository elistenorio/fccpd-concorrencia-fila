from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Produto, Pedido
from app.queue import fila_pedidos
from app.schemas import PedidoCreate, PedidoResponse, PedidoStatusResponse
from app.tasks import processar_pedido

Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/pedidos", response_model=PedidoResponse, status_code=201)
def criar_pedido(pedido_in: PedidoCreate, db: Session = Depends(get_db)):

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

    fila_pedidos.enqueue(processar_pedido, pedido.id)

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
