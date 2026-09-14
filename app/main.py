from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Produto, Pedido

Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/orders")
def criar_pedido(produto_id: int, db: Session = Depends(get_db)):
    produto = (
        db.query(Produto)
        .filter(Produto.id == produto_id)
        .with_for_update()
        .first()
    )

    if produto is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    if produto.estoque <= 0:
        raise HTTPException(status_code=409, detail="Produto sem estoque")

    produto.estoque -= 1
    pedido = Pedido(produto_id=produto.id, status="confirmado")
    db.add(pedido)
    db.commit()
    db.refresh(pedido)

    return {"pedido_id": pedido.id, "estoque_restante": produto.estoque}