from redis import Redis
from rq import Queue, Worker

from app.queue import REDIS_URL

if __name__ == "__main__":
    # Conexão própria, sem o timeout curto da conexão de publicação: o worker
    # fica bloqueado esperando jobs, e o RQ ajusta o timeout dele sozinho.
    conexao = Redis.from_url(REDIS_URL)
    fila = Queue("pedidos", connection=conexao)

    # with_scheduler: necessário para o RQ reenfileirar os jobs com retry
    # agendado (Retry com interval). Só um dos workers assume o agendador.
    worker = Worker([fila], connection=conexao)
    worker.work(with_scheduler=True)
