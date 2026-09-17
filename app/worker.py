from app.queue import fila_pedidos, redis_conn
from rq import Worker

if __name__ == "__main__":
    worker = Worker([fila_pedidos], connection=redis_conn)
    worker.work()