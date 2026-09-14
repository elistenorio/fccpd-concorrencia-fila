from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Produto, Pedido
from app.schemas import PedidoCreate, PedidoResponse

Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/pedidos", response_model=PedidoResponse, status_code=201)
def criar_pedido(pedido_in: PedidoCreate, db: Session = Depends(get_db)):

    produto = (
        db.query(Produto)
        .filter(Produto.id == pedido_in.produto_id)
        .with_for_update()
        .first()
    )

    if produto is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    if produto.estoque < 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Produto sem estoque")

    produto.estoque -= 1

    pedido = Pedido(produto_id=produto.id)
    db.add(pedido)
    db.commit()
    db.refresh(pedido)

    return PedidoResponse(
        pedido_id=pedido.id,
        produto_id=produto.id,
        mensagem="Pedido criado com sucesso",
    )
