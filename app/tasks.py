import time
from app.database import SessionLocal
from app.models import Pedido


def processar_pedido(pedido_id: int):
    db = SessionLocal()
    try:
        pedido = db.get(Pedido, pedido_id)

        # Idempotência: com retry e reconciliador, o mesmo pedido pode ser
        # entregue mais de uma vez. Só faz o trabalho se ainda estiver pendente,
        # para não emitir a nota fiscal duas vezes.
        if pedido is None or pedido.status != "pendente":
            return

        time.sleep(3)  # simula um trabalho pesado (ex.: emitir nota fiscal)

        # UPDATE condicional: se o pedido foi apagado ou já processado por
        # outra entrega enquanto este worker trabalhava, não sobrescreve nada.
        db.query(Pedido).filter(
            Pedido.id == pedido_id, Pedido.status == "pendente"
        ).update({"status": "processado"})
        db.commit()
    finally:
        db.close()


def marcar_pedido_como_falho(job, connection, tipo, valor, tb):
    """Callback do RQ chamado a cada falha do job.

    O RQ chama este callback antes de decidir se vai tentar de novo, então
    enquanto sobrarem tentativas o pedido continua "pendente". Só quando elas
    acabam o pedido vira "falhou", em vez de ficar pendente para sempre.
    """
    if job.retries_left:
        return

    pedido_id = job.args[0]
    db = SessionLocal()
    try:
        db.query(Pedido).filter(
            Pedido.id == pedido_id, Pedido.status == "pendente"
        ).update({"status": "falhou"})
        db.commit()
    finally:
        db.close()
