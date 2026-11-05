"""
Cliente HTTP da api para o serviço de recomendações (comunicação síncrona).

Recomendação é um extra da página do produto: se o serviço estiver fora ou
lento, a página sai sem recomendações em vez de falhar ou travar. Mesma ideia
da fila na Parte 1 (timeout curto + disjuntor), aplicada a uma chamada REST.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as PrazoEsgotado

import httpx

RECOMENDACOES_URL = os.getenv("RECOMENDACOES_URL", "http://recomendacoes:8001")
TIMEOUT_SEGUNDOS = 1.0
# Depois de uma falha, a api passa um tempo sem tentar (disjuntor).
PAUSA_APOS_FALHA_SEGUNDOS = 15

logger = logging.getLogger("uvicorn.error")
_cliente = httpx.Client(base_url=RECOMENDACOES_URL, timeout=TIMEOUT_SEGUNDOS)
_fora_ate = 0.0

# Com o container parado, a resolução DNS do nome "recomendacoes" leva ~7 s e
# não respeita o timeout do httpx (medido; é o mesmo problema do Redis na
# Parte 1). Por isso a chamada roda numa thread à parte e a api espera no
# máximo TIMEOUT_SEGUNDOS no total: se estourar, responde sem recomendações e
# a thread termina sozinha depois. Poucas threads, porque com o disjuntor
# aberto ninguém mais é enviado.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="recomendacoes")


def _get(produto_id: int, limite: int, request_id: str):
    resposta = _cliente.get(
        f"/recomendacoes/{produto_id}",
        params={"limite": limite},
        headers={"X-Request-ID": request_id},
    )
    resposta.raise_for_status()
    return resposta


def buscar_recomendacoes(produto_id: int, limite: int, request_id: str):
    """Lista de recomendações, ou None se o serviço estiver indisponível."""
    global _fora_ate
    if time.monotonic() < _fora_ate:
        logger.info("[%s] Recomendações em pausa (disjuntor aberto); produto %s sem recomendações", request_id, produto_id)
        return None

    inicio = time.perf_counter()
    try:
        resposta = _executor.submit(_get, produto_id, limite, request_id).result(timeout=TIMEOUT_SEGUNDOS)
    except (httpx.HTTPError, PrazoEsgotado) as erro:
        _fora_ate = time.monotonic() + PAUSA_APOS_FALHA_SEGUNDOS
        motivo = "prazo esgotado" if isinstance(erro, PrazoEsgotado) else f"{type(erro).__name__}: {erro}"
        logger.warning(
            "[%s] Serviço de recomendações indisponível (%s) após %.0f ms; disjuntor aberto por %ss",
            request_id, motivo, (time.perf_counter() - inicio) * 1000, PAUSA_APOS_FALHA_SEGUNDOS,
        )
        return None

    recomendacoes = resposta.json()["recomendacoes"]
    logger.info(
        "[%s] GET %s/recomendacoes/%s -> %s, %s recomendações em %.0f ms",
        request_id, RECOMENDACOES_URL, produto_id, resposta.status_code,
        len(recomendacoes), (time.perf_counter() - inicio) * 1000,
    )
    return recomendacoes
