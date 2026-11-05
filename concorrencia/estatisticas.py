"""
Estado compartilhado pelos consumidores: onde a condição de corrida aparece.

Cada consumidor, ao processar um item, faz "ler total -> somar 1 -> gravar
total". Isso são três passos, não uma operação atômica. Se duas threads lerem
o mesmo valor antes de qualquer uma gravar, uma das atualizações se perde
(lost update).
"""

import threading
import time


def _troca_de_contexto():
    # O SO pode tirar a thread da CPU entre quaisquer dois passos. No CPython,
    # por causa do GIL, essa troca no meio de um "x = x + 1" é rara, o que deixa
    # o bug intermitente: some nos testes e aparece em produção. sleep(0) cede
    # a CPU exatamente no meio da seção crítica para a demonstração ser
    # reproduzível; a correção (o lock) é a mesma com ou sem ele.
    time.sleep(0)


class EstatisticasInseguras:
    def __init__(self):
        self.total = 0
        self.soma = 0

    def registrar(self, valor):
        total = self.total
        soma = self.soma
        _troca_de_contexto()
        self.total = total + 1
        self.soma = soma + valor


class EstatisticasSeguras(EstatisticasInseguras):
    """Mesma seção crítica, agora protegida por um lock: só uma thread por vez."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def registrar(self, valor):
        with self._lock:
            super().registrar(valor)
