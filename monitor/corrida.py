"""
A demonstração de concorrencia/demo_corrida.py, medida para o painel.

Roda o mesmo pipeline produtor-consumidor com EstatisticasInseguras e com
EstatisticasSeguras e, enquanto ele roda, uma thread amostradora registra a
ocupação do buffer, quantos itens os consumidores retiraram e quantos as
estatísticas contaram. Sem lock, a distância entre "retirados" e "contados" são
as atualizações perdidas, aparecendo em tempo real no gráfico.
"""

import threading
import time

from concorrencia.buffer import BufferLimitado
from concorrencia.demo_corrida import FIM, consumidor, produtor
from concorrencia.estatisticas import EstatisticasInseguras, EstatisticasSeguras
from estruturas import ListaEncadeada

MAX_PONTOS = 300


class _BufferContado(BufferLimitado):
    """BufferLimitado que conta os itens retirados (sem contar os sinais de FIM)."""

    def __init__(self, capacidade):
        super().__init__(capacidade)
        self.retirados = 0
        self._lock_contagem = threading.Lock()

    def retirar(self, timeout=None):
        item = super().retirar(timeout)
        if item is not FIM:
            with self._lock_contagem:
                self.retirados += 1
        return item


def _rodar(classe, n_produtores, n_consumidores, itens, capacidade):
    estatisticas = classe()
    buffer = _BufferContado(capacidade)
    pontos = ListaEncadeada()
    terminou = threading.Event()
    inicio = time.perf_counter()

    def amostrar():
        while not terminou.is_set():
            pontos.inserir_fim((round((time.perf_counter() - inicio) * 1000, 1), len(buffer), buffer.retirados, estatisticas.total))
            time.sleep(0.002)

    produtores = [None] * n_produtores
    for i in range(n_produtores):
        produtores[i] = threading.Thread(target=produtor, args=(buffer, 1 + i * itens, itens))
    consumidores = [None] * n_consumidores
    for i in range(n_consumidores):
        consumidores[i] = threading.Thread(target=consumidor, args=(buffer, estatisticas))

    amostrador = threading.Thread(target=amostrar)
    amostrador.start()
    for t in consumidores:
        t.start()
    for t in produtores:
        t.start()
    for t in produtores:
        t.join()
    for _ in range(n_consumidores):
        buffer.colocar(FIM)
    for t in consumidores:
        t.join()
    duracao = time.perf_counter() - inicio
    terminou.set()
    amostrador.join()
    pontos.inserir_fim((round(duracao * 1000, 1), len(buffer), buffer.retirados, estatisticas.total))

    todos = pontos.para_array()
    passo = max(1, len(todos) // MAX_PONTOS)
    serie = ListaEncadeada()
    for i in range(0, len(todos), passo):
        serie.inserir_fim(todos[i])
    if passo > 1:
        serie.inserir_fim(todos[-1])

    total_itens = n_produtores * itens
    soma_esperada = total_itens * (total_itens + 1) // 2
    return {
        "total": estatisticas.total,
        "soma": estatisticas.soma,
        "retirados": buffer.retirados,
        "perdidas": total_itens - estatisticas.total,
        "correto": estatisticas.total == total_itens and estatisticas.soma == soma_esperada,
        "ms": round(duracao * 1000, 1),
        "serie": serie.para_array(),
    }


def executar_corrida(produtores=3, consumidores=3, itens=2000, capacidade=16):
    total_itens = produtores * itens
    return {
        "parametros": {"produtores": produtores, "consumidores": consumidores, "itens": itens, "capacidade": capacidade},
        "esperado": {"total": total_itens, "soma": total_itens * (total_itens + 1) // 2},
        "sem_lock": _rodar(EstatisticasInseguras, produtores, consumidores, itens, capacidade),
        "com_lock": _rodar(EstatisticasSeguras, produtores, consumidores, itens, capacidade),
    }
