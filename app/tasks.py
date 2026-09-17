import time
from app.database import SessionLocal
from app.models import Pedido


def processar_pedido(pedido_id: int):
    db = SessionLocal()
    try:
        pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
        if pedido is None:
            return

        time.sleep(3)  # simula um trabalho pesado (ex.: emitir nota fiscal)

        pedido.status = "processado"
        db.commit()
    finally:
        db.close()
