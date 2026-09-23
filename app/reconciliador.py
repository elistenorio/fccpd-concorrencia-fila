"""
Reconciliador: garante que nenhum pedido fique "pendente" para sempre.

Se o Redis estava fora na hora da compra, a API grava o pedido mas não consegue
publicar o job. De tempos em tempos, este processo procura pedidos pendentes há
mais de IDADE_MINIMA_SEGUNDOS que não têm job no Redis e os publica na fila.
"""

import logging
import time
from datetime import timedelta

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Pedido
from app.queue import pedido_tem_job, publicar_pedido

INTERVALO_SEGUNDOS = 5
# Margem para a própria API publicar o job logo após o COMMIT; o reconciliador
# só age sobre pedidos que claramente ficaram sem job.
IDADE_MINIMA_SEGUNDOS = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reconciliador")


def reconciliar() -> list[int]:
    db = SessionLocal()
    try:
        limite = func.now() - timedelta(seconds=IDADE_MINIMA_SEGUNDOS)
        pendentes = [
            pedido_id
            for (pedido_id,) in db.query(Pedido.id)
            .filter(Pedido.status == "pendente", Pedido.criado_em < limite)
            .order_by(Pedido.id)
        ]
    finally:
        db.close()

    publicados = []
    for pedido_id in pendentes:
        if not pedido_tem_job(pedido_id):
            publicar_pedido(pedido_id)
            publicados.append(pedido_id)
    return publicados


if __name__ == "__main__":
    logger.info("Reconciliador iniciado (a cada %ss)", INTERVALO_SEGUNDOS)
    while True:
        try:
            publicados = reconciliar()
            if publicados:
                logger.info("Pedidos sem job republicados na fila: %s", publicados)
        except Exception as erro:
            # Redis ou banco fora: só registra e tenta de novo no próximo ciclo.
            logger.warning("Não foi possível reconciliar agora: %s", erro)
        time.sleep(INTERVALO_SEGUNDOS)
