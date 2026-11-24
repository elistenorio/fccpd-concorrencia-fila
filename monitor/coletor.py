"""
Coletor concorrente.

Três threads produtoras, uma por nível, coletam em paralelo e entregam as
amostras a um ``BufferLimitado`` (a fila circular própria protegida por um
``Lock`` e duas ``Condition``). Uma thread consumidora retira do buffer e
aplica no ``EstadoMonitor``.

**Por que threads e não um laço só.** Os três níveis têm custos e ritmos
diferentes: ler ``/proc`` leva milissegundos, a API do Docker leva centenas de
milissegundos (duas chamadas por container) e um health check de serviço fora
do ar leva até o timeout inteiro. Num laço único, o nível 1 ficaria preso
esperando o nível 3, e uma pausa de 2 s num health check viraria um buraco de
2 s na curva de CPU. Com uma thread por nível, cada um coleta no seu intervalo.

**Por que threads servem aqui, apesar do GIL.** Os três produtores passam a
maior parte do tempo esperando I/O (arquivo, socket do Docker, resposta HTTP), e
o CPython libera o GIL durante a espera. É o oposto do cálculo de recomendações
da FCCPD, que é puro CPU e por isso usa processos.

**Por que um buffer entre produtores e consumidor.** As estruturas do estado
(hash, árvore, filas) não são thread-safe. Com um só consumidor escrevendo, as
regras de evento ("mudou de estado desde a última amostra?") rodam numa ordem
só, sem duas threads comparando com o mesmo estado anterior ao mesmo tempo. O
buffer também absorve rajadas: se o consumidor atrasa, os produtores
encontram o buffer cheio e esperam, em vez de acumular memória sem limite.
"""

import logging
import os
import threading
import time

from concorrencia.buffer import BufferLimitado
from monitor.estado import EstadoMonitor
from monitor.eventos import Evento
from monitor.nivel1_so import ColetorSO
from monitor.nivel2_containers import ColetorContainers
from monitor.nivel3_servicos import SERVICOS_PADRAO, ColetorServicos, parse_servicos

logger = logging.getLogger("monitor")

# Intervalo de coleta de cada nível, em segundos.
INTERVALO_NIVEL1 = float(os.getenv("MONITOR_INTERVALO_SO", "2"))
INTERVALO_NIVEL2 = float(os.getenv("MONITOR_INTERVALO_CONTAINERS", "5"))
INTERVALO_NIVEL3 = float(os.getenv("MONITOR_INTERVALO_SERVICOS", "5"))
# Capacidade do buffer: 3 produtores precisam caber mesmo se o consumidor
# atrasar alguns ciclos.
CAPACIDADE_BUFFER = int(os.getenv("MONITOR_BUFFER", "32"))


class Coletor:
    """
    Ciclo de vida: ``iniciar()`` sobe as 4 threads, ``parar()`` pede que elas
    terminem e espera. Todas são ``daemon`` para não segurar o processo se o
    uvicorn morrer sem chamar o shutdown.
    """

    def __init__(self, estado=None, servicos=None, buffer=None):
        self.estado = estado or EstadoMonitor()
        self.buffer = buffer or BufferLimitado(CAPACIDADE_BUFFER)
        self._parar = threading.Event()
        self._threads = []

        if servicos is None:
            do_ambiente = os.getenv("MONITOR_SERVICOS")
            servicos = parse_servicos(do_ambiente) if do_ambiente else SERVICOS_PADRAO

        self._so = ColetorSO()
        # Sem MONITOR_PROJETO, o nível 2 descobre sozinho a que projeto Compose
        # o monitor pertence e mostra só os containers desse projeto. Com
        # MONITOR_PROJETO=TODOS, mostra todos os containers da máquina; com um
        # nome, mostra os daquele projeto.
        projeto = os.getenv("MONITOR_PROJETO") or None
        if projeto == "TODOS":
            self._containers = ColetorContainers(descobrir_projeto=False)
        else:
            self._containers = ColetorContainers(rotulo_projeto=projeto)
        self._servicos = ColetorServicos(servicos)

        # Contadores por thread, só para o painel mostrar que todas estão vivas.
        self.estatisticas = {
            "nivel1": {"coletas": 0, "erros": 0, "ultimo_erro": None},
            "nivel2": {"coletas": 0, "erros": 0, "ultimo_erro": None},
            "nivel3": {"coletas": 0, "erros": 0, "ultimo_erro": None},
            "consumidor": {"aplicadas": 0, "eventos": 0},
        }

    # ------------------------------------------------------------- ciclo de vida

    def iniciar(self):
        if self._threads:
            return
        alvos = [
            ("monitor-nivel1", self._produzir, (1, self._so.coletar, INTERVALO_NIVEL1, "nivel1")),
            ("monitor-nivel2", self._produzir, (2, self._containers.coletar, INTERVALO_NIVEL2, "nivel2")),
            ("monitor-nivel3", self._produzir, (3, self._servicos.coletar, INTERVALO_NIVEL3, "nivel3")),
            ("monitor-consumidor", self._consumir, ()),
        ]
        for nome, alvo, args in alvos:
            thread = threading.Thread(target=alvo, args=args, name=nome, daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("Coletor iniciado: %s threads", len(self._threads))

    def parar(self, timeout=5.0):
        self._parar.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads = []
        self._containers.fechar()
        self._servicos.fechar()
        logger.info("Coletor parado")

    @property
    def vivo(self):
        return any(t.is_alive() for t in self._threads)

    def threads_vivas(self):
        return [{"nome": t.name, "viva": t.is_alive()} for t in self._threads]

    # --------------------------------------------------------------- produtores

    def _produzir(self, nivel, coletar, intervalo, rotulo):
        """
        Laço de uma thread produtora. Um erro numa coleta não mata a thread:
        vira evento no painel e o laço continua, senão uma falha momentânea do
        Docker deixaria a tela 2 congelada para sempre.
        """
        while not self._parar.is_set():
            inicio = time.perf_counter()
            try:
                amostra = coletar()
                amostra.setdefault("timestamp", time.time())
                amostra["duracao_coleta_ms"] = round((time.perf_counter() - inicio) * 1000, 1)
                # timeout: se o consumidor travar, a thread não fica presa aqui
                # para sempre; perde uma amostra e tenta na próxima volta.
                self.buffer.colocar((nivel, amostra), timeout=intervalo)
                self.estatisticas[rotulo]["coletas"] += 1
            except TimeoutError:
                self.estatisticas[rotulo]["erros"] += 1
                self.estatisticas[rotulo]["ultimo_erro"] = "buffer cheio: amostra descartada"
                logger.warning("Nível %s: buffer cheio, amostra descartada", nivel)
            except Exception as erro:  # noqa: BLE001 - a thread não pode morrer
                self.estatisticas[rotulo]["erros"] += 1
                self.estatisticas[rotulo]["ultimo_erro"] = f"{type(erro).__name__}: {erro}"
                logger.exception("Falha na coleta do nível %s", nivel)
                self.estado.registrar_evento_proprio(
                    Evento(
                        nivel=nivel,
                        chave=f"coletor:nivel{nivel}",
                        severidade="alerta",
                        mensagem=f"Falha ao coletar o nível {nivel}: {type(erro).__name__}",
                    )
                )
            # Espera no Event, e não em sleep: parar() acorda a thread na hora.
            self._parar.wait(intervalo)

    # --------------------------------------------------------------- consumidor

    def _consumir(self):
        while not self._parar.is_set():
            try:
                item = self.buffer.retirar(timeout=1.0)
            except TimeoutError:
                continue  # nenhuma amostra nesse segundo; reconfere o parar
            nivel, amostra = item
            try:
                eventos = self.estado.aplicar(nivel, amostra)
                self.estatisticas["consumidor"]["aplicadas"] += 1
                self.estatisticas["consumidor"]["eventos"] += len(eventos)
                for evento in eventos:
                    logger.info("[nível %s] %s: %s", evento.nivel, evento.severidade, evento.mensagem)
            except Exception:  # noqa: BLE001 - idem: o consumidor não pode morrer
                logger.exception("Falha ao aplicar amostra do nível %s", nivel)
