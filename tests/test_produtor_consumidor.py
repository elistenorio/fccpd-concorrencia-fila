import threading

import pytest

from concorrencia import BufferLimitado, EstatisticasInseguras, EstatisticasSeguras
from concorrencia.demo_corrida import executar


def test_buffer_entrega_cada_item_exatamente_uma_vez():
    buffer = BufferLimitado(4)
    recebidos = [[] for _ in range(3)]
    n_produtores, por_produtor = 4, 500

    def produzir(inicio):
        for v in range(inicio, inicio + por_produtor):
            buffer.colocar(v)

    def consumir(destino):
        while True:
            item = buffer.retirar()
            if item is None:
                return
            destino.append(item)

    consumidores = [threading.Thread(target=consumir, args=(r,)) for r in recebidos]
    produtores = [threading.Thread(target=produzir, args=(i * por_produtor,)) for i in range(n_produtores)]
    for t in consumidores + produtores:
        t.start()
    for t in produtores:
        t.join()
    for _ in consumidores:
        buffer.colocar(None)
    for t in consumidores:
        t.join()

    todos = sorted(v for r in recebidos for v in r)
    assert todos == list(range(n_produtores * por_produtor))


def test_buffer_mantem_ordem_fifo():
    buffer = BufferLimitado(3)
    for v in (1, 2, 3):
        buffer.colocar(v)
    assert [buffer.retirar() for _ in range(3)] == [1, 2, 3]


def test_buffer_cheio_bloqueia_produtor():
    buffer = BufferLimitado(1)
    buffer.colocar("a")
    with pytest.raises(TimeoutError):
        buffer.colocar("b", timeout=0.05)


def test_buffer_vazio_bloqueia_consumidor():
    with pytest.raises(TimeoutError):
        BufferLimitado(1).retirar(timeout=0.05)


def test_consumidor_acorda_quando_produtor_coloca():
    buffer = BufferLimitado(1)
    resultado = []
    consumidor = threading.Thread(target=lambda: resultado.append(buffer.retirar(timeout=2)))
    consumidor.start()
    buffer.colocar("item")
    consumidor.join()
    assert resultado == ["item"]


def test_sem_lock_perde_atualizacoes():
    estatisticas = EstatisticasInseguras()
    executar(estatisticas, n_produtores=3, n_consumidores=4, itens_por_produtor=1000, capacidade=16)
    assert estatisticas.total < 3000


def test_com_lock_nao_perde_nada():
    estatisticas = EstatisticasSeguras()
    executar(estatisticas, n_produtores=3, n_consumidores=4, itens_por_produtor=1000, capacidade=16)
    assert estatisticas.total == 3000
    assert estatisticas.soma == 3000 * 3001 // 2
