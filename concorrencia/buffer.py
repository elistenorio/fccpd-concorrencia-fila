"""Buffer limitado thread-safe sobre a FilaCircular própria."""

import threading

from estruturas.fila_circular import FilaCircular


class BufferLimitado:
    """
    Buffer do problema produtor-consumidor.

    A FilaCircular sozinha NÃO é thread-safe: enfileirar lê ``_tamanho``,
    escreve no array e só depois incrementa ``_tamanho``. Duas threads no meio
    disso podem gravar na mesma posição e perder um item. Aqui toda operação
    roda com o lock, e duas variáveis de condição (sobre o MESMO lock) fazem
    as threads dormirem em vez de ficar em espera ocupada:

    - produtor com buffer cheio  -> espera em ``_nao_cheio``
    - consumidor com buffer vazio -> espera em ``_nao_vazio``

    Cada lado acorda o outro com notify() depois de mexer na fila. O wait_for
    reconfere a condição ao acordar, o que protege contra acordar sem motivo
    (spurious wakeup) e contra outra thread ter pegado o item antes.
    """

    def __init__(self, capacidade: int):
        self._fila = FilaCircular(capacidade)
        self._lock = threading.Lock()
        self._nao_cheio = threading.Condition(self._lock)
        self._nao_vazio = threading.Condition(self._lock)

    def __len__(self):
        with self._lock:
            return len(self._fila)

    @property
    def capacidade(self) -> int:
        return self._fila.capacidade

    def colocar(self, item, timeout: float | None = None):
        """Bloqueia enquanto o buffer estiver cheio. TimeoutError se passar do timeout."""
        with self._nao_cheio:
            if not self._nao_cheio.wait_for(lambda: not self._fila.cheia(), timeout):
                raise TimeoutError("buffer continua cheio")
            self._fila.enfileirar(item)
            self._nao_vazio.notify()

    def retirar(self, timeout: float | None = None):
        """Bloqueia enquanto o buffer estiver vazio. TimeoutError se passar do timeout."""
        with self._nao_vazio:
            if not self._nao_vazio.wait_for(lambda: not self._fila.vazia(), timeout):
                raise TimeoutError("buffer continua vazio")
            item = self._fila.desenfileirar()
            self._nao_cheio.notify()
            return item
