"""Recálculo em lote: carrega do banco, calcula (seq/threads/processos) e grava."""

import logging
import os
import time

from sqlalchemy import delete, insert, select

from app.database import SessionLocal
from app.models import Pedido, Produto
from recomendacoes import calculo
from recomendacoes.models import Recomendacao

logger = logging.getLogger("recomendacoes")


def carregar_dados(db):
    """
    Uma consulta para os produtos e outra para os pares (produto, cliente).
    Tudo é carregado UMA vez aqui no processo principal; os processos do
    cálculo nunca abrem conexão com o banco.
    """
    produtos = db.execute(
        select(Produto.id, Produto.categoria, Produto.artesao).order_by(Produto.id)
    ).all()
    posicao = {pid: i for i, (pid, _, _) in enumerate(produtos)}
    compradores = [set() for _ in produtos]
    pares = db.execute(
        select(Pedido.produto_id, Pedido.cliente_id)
        .where(Pedido.cliente_id.is_not(None), Pedido.status != "falhou")
        .distinct()
    )
    for produto_id, cliente_id in pares:
        compradores[posicao[produto_id]].add(cliente_id)
    return (
        [p.id for p in produtos],
        [frozenset(c) for c in compradores],
        [p.categoria for p in produtos],
        [p.artesao for p in produtos],
    )


def gravar(db, ids, recomendacoes):
    linhas = [
        {"produto_id": ids[i], "recomendado_id": ids[j], "posicao": pos + 1, "score": score}
        for i, recs in enumerate(recomendacoes)
        for pos, (j, score) in enumerate(recs)
    ]
    # DELETE + INSERT na mesma transação: quem consulta durante o recálculo
    # continua vendo as recomendações antigas até o COMMIT.
    db.execute(delete(Recomendacao))
    if linhas:
        db.execute(insert(Recomendacao), linhas)
    db.commit()
    return len(linhas)


def recalcular(modo="processos", workers=None, k=10):
    workers = workers or os.cpu_count() or 1
    db = SessionLocal()
    try:
        t0 = time.perf_counter()
        ids, compradores, categorias, artesaos = carregar_dados(db)
        t1 = time.perf_counter()
        recomendacoes = calculo.calcular(compradores, categorias, artesaos, modo=modo, workers=workers, k=k)
        t2 = time.perf_counter()
        gravadas = gravar(db, ids, recomendacoes)
        t3 = time.perf_counter()
    finally:
        db.close()

    resumo = {
        "modo": modo,
        "workers": 1 if modo == "sequencial" else workers,
        "cpus": os.cpu_count(),
        "produtos": len(ids),
        "comparacoes": len(ids) * (len(ids) - 1),
        "recomendacoes_gravadas": gravadas,
        "tempo_carga_s": round(t1 - t0, 3),
        "tempo_calculo_s": round(t2 - t1, 3),
        "tempo_gravacao_s": round(t3 - t2, 3),
        "tempo_total_s": round(t3 - t0, 3),
    }
    logger.info(
        "Recálculo %s com %s worker(s): %s produtos, cálculo %.2fs (carga %.2fs, gravação %.2fs)",
        resumo["modo"], resumo["workers"], resumo["produtos"],
        resumo["tempo_calculo_s"], resumo["tempo_carga_s"], resumo["tempo_gravacao_s"],
    )
    return resumo
