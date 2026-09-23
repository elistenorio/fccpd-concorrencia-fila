import os
import time

from redis import Redis
from redis.exceptions import RedisError
from rq import Callback, Queue, Retry
from rq.job import Job

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Conexão de quem PUBLICA na fila (API e reconciliador). Os timeouts curtos
# evitam que uma compra fique presa esperando um Redis travado. O worker usa
# uma conexão própria (ver app/worker.py), porque ele fica bloqueado de
# propósito esperando jobs e não pode ter esse timeout.
redis_conn = Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
fila_pedidos = Queue("pedidos", connection=redis_conn)

# Se o job falhar (ex.: banco fora do ar), o RQ tenta de novo com espera
# crescente entre as tentativas antes de desistir e marcar o pedido "falhou".
RETRY_PEDIDO = Retry(max=3, interval=[5, 15, 30])

# Disjuntor (circuit breaker): depois de uma falha do Redis, a API passa um
# tempo sem tentar publicar e deixa esses pedidos para o reconciliador. Sem
# isso, com o Redis fora, cada compra prenderia uma thread da API por segundos
# tentando conectar (só a resolução DNS do host "redis" leva ~4 s e não
# respeita timeout), até esgotar as threads e travar a loja inteira.
PAUSA_APOS_FALHA_SEGUNDOS = 15
_fila_fora_ate = 0.0


def job_id_do_pedido(pedido_id: int) -> str:
    # Id fixo por pedido: permite ao reconciliador saber se o pedido já tem
    # job no Redis, sem publicar duplicado.
    return f"pedido-{pedido_id}"


def publicar_pedido(pedido_id: int):
    # A tarefa é referenciada pelo caminho (string), então a API não precisa
    # importar o código do worker.
    fila_pedidos.enqueue(
        "app.tasks.processar_pedido",
        pedido_id,
        job_id=job_id_do_pedido(pedido_id),
        retry=RETRY_PEDIDO,
        on_failure=Callback("app.tasks.marcar_pedido_como_falho"),
    )


def tentar_publicar_pedido(pedido_id: int) -> bool:
    """Publica o pedido sem nunca derrubar a compra.

    Retorna False se o Redis estiver indisponível; nesse caso o pedido continua
    "pendente" no banco e o reconciliador publica o job quando a fila voltar.
    """
    global _fila_fora_ate
    if time.monotonic() < _fila_fora_ate:
        return False
    try:
        publicar_pedido(pedido_id)
        return True
    except RedisError:
        _fila_fora_ate = time.monotonic() + PAUSA_APOS_FALHA_SEGUNDOS
        return False


def pedido_tem_job(pedido_id: int) -> bool:
    return Job.exists(job_id_do_pedido(pedido_id), connection=redis_conn)
