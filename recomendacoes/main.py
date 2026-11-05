"""
Serviço de recomendações (porta 8001).

Separado da api porque o recálculo é pesado (n² comparações, todos os núcleos
ocupados por segundos): rodando no mesmo processo do checkout, deixaria as
compras lentas justamente enquanto calcula. Aqui ele escala e cai sozinho; a
consulta de recomendações é só uma leitura de tabela pré-calculada.
"""

import logging
import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine, get_db
from app.models import Produto
from recomendacoes.calculo import MODOS
from recomendacoes.models import Recomendacao
from recomendacoes.servico import recalcular

# Cria só a tabela deste serviço. produtos e pedidos são da api, que sobe
# antes (depends_on com healthcheck no compose).
Recomendacao.__table__.create(bind=engine, checkfirst=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("recomendacoes")

app = FastAPI(title="Origem — Recomendações")

# Um recálculo por vez: dois em paralelo disputariam os mesmos núcleos e
# apagariam a tabela um do outro.
_recalculo_em_andamento = threading.Lock()


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/recomendacoes/{produto_id}")
def consultar(
    produto_id: int,
    limite: int = Query(5, ge=1, le=10),
    x_request_id: str = Header("-"),
    db: Session = Depends(get_db),
):
    if db.get(Produto, produto_id) is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    linhas = db.execute(
        select(Recomendacao.recomendado_id, Produto.nome, Recomendacao.score)
        .join(Produto, Produto.id == Recomendacao.recomendado_id)
        .where(Recomendacao.produto_id == produto_id)
        .order_by(Recomendacao.posicao)
        .limit(limite)
    ).all()
    logger.info("[%s] Consulta de recomendações do produto %s: %s encontradas", x_request_id, produto_id, len(linhas))
    return {
        "produto_id": produto_id,
        "recomendacoes": [
            {"produto_id": rid, "nome": nome, "score": round(score, 4)} for rid, nome, score in linhas
        ],
    }


@app.post("/recomendacoes/recalcular")
def recalcular_endpoint(
    modo: str = Query("processos", enum=list(MODOS)),
    workers: int | None = Query(None, ge=1, le=64),
    k: int = Query(10, ge=1, le=50),
):
    if not _recalculo_em_andamento.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Já existe um recálculo em andamento")
    try:
        return recalcular(modo=modo, workers=workers, k=k)
    finally:
        _recalculo_em_andamento.release()
