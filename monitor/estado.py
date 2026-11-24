"""
Estado do monitor: onde as estruturas de dados próprias (AED) são usadas.

Uma única thread escreve aqui (a consumidora do coletor) e várias leem (cada
requisição do painel é atendida numa thread do uvicorn). Nenhuma das estruturas
é thread-safe por si, então toda operação roda sob ``_lock``. As leituras
copiam o que devolvem, para o painel nunca iterar uma estrutura que está sendo
alterada no meio da resposta.

| Estrutura | Onde | Por quê |
|---|---|---|
| ``FilaCircular(sobrescrever=True)`` | janela das últimas N amostras de cada nível | memória fixa: o monitor roda por horas e o histórico nunca cresce |
| ``TabelaHash`` | estado atual por container e por serviço | o painel pede "como está o worker-2": acesso por chave em O(1) |
| ``ArvoreBusca`` | eventos indexados por (horário, sequência) | consulta por intervalo ("últimos 5 min") em O(h + k) |
| ``Fila`` | alertas ainda não exibidos | fila sem limite: amostra de métrica pode ser descartada, alerta não |
| ``merge_sort`` | linha do tempo de eventos | estável: eventos do mesmo instante mantêm a ordem de chegada |
| ``quick_sort`` | ordenar containers e processos por CPU/memória | ordenação da tela, O(n log n) médio |
| ``busca_binaria`` | achar a primeira amostra a partir de um horário | histórico já está em ordem crescente de tempo |
| ``busca_sequencial`` | achar um processo pelo nome na amostra | lista não ordenada por nome |
"""

import threading
import time

from estruturas.arvore_busca import ArvoreBusca
from estruturas.busca import busca_binaria, busca_sequencial
from estruturas.fila import Fila
from estruturas.fila_circular import FilaCircular
from estruturas.ordenacao import merge_sort, quick_sort
from estruturas.tabela_hash import TabelaHash
from monitor.eventos import eventos_do_container, eventos_do_host, eventos_do_servico

# Amostras guardadas por nível. Com coleta a cada 2 s, 300 amostras = 10 min.
JANELA_AMOSTRAS = 300
# Eventos guardados na árvore; acima disso, os mais antigos são removidos.
MAX_EVENTOS = 500


class EstadoMonitor:
    """Memória do monitor: últimas amostras, estado por chave e eventos."""

    def __init__(self, janela=JANELA_AMOSTRAS, max_eventos=MAX_EVENTOS):
        self._lock = threading.Lock()
        self._janela = janela
        self._max_eventos = max_eventos

        # Nível -> janela das últimas amostras (memória fixa).
        self._historico = TabelaHash(capacidade_inicial=8)
        for nivel in (1, 2, 3):
            self._historico.inserir(nivel, FilaCircular(janela, sobrescrever=True))

        self._containers = TabelaHash()   # nome -> último estado do container
        self._servicos = TabelaHash()     # nome -> último estado do serviço
        self._situacoes = TabelaHash()    # chave de evento -> situação anterior
        self._eventos = ArvoreBusca()     # (timestamp, sequência) -> Evento
        self._alertas = Fila()            # alertas pendentes de exibição
        self._sequencia = 0
        self._amostras_recebidas = 0
        self._inicio = time.time()

    # ------------------------------------------------------------------ escrita

    def aplicar(self, nivel, amostra):
        """
        Registra uma amostra e devolve os eventos que ela gerou. Chamado só pela
        thread consumidora do coletor.
        """
        with self._lock:
            self._amostras_recebidas += 1
            self._historico.obter(nivel).enfileirar(amostra)

            if nivel == 1:
                eventos = self._aplicar_host(amostra)
            elif nivel == 2:
                eventos = self._aplicar_containers(amostra)
            else:
                eventos = self._aplicar_servicos(amostra)

            for evento in eventos:
                self._registrar(evento)
            return eventos

    def _aplicar_host(self, amostra):
        anterior = self._situacoes.obter("host", None)
        eventos, situacao = eventos_do_host(amostra, anterior)
        self._situacoes.inserir("host", situacao)
        return eventos

    def _aplicar_containers(self, amostra):
        if not amostra.get("disponivel"):
            return []
        eventos = []
        vistos = TabelaHash()
        for container in amostra.get("containers") or []:
            container = dict(container, timestamp=amostra.get("timestamp"))
            nome = container["nome"]
            vistos.inserir(nome, True)
            self._containers.inserir(nome, container)
            chave = f"container:{nome}"
            novos, situacao = eventos_do_container(container, self._situacoes.obter(chave, None))
            self._situacoes.inserir(chave, situacao)
            eventos.extend(novos)

        # Container que saiu da lista (removido, não só parado) deixa de ser
        # mostrado: o estado dele não vale mais nada.
        for nome in [n for n in self._containers.chaves() if not vistos.contem(n)]:
            self._containers.remover(nome)
        return eventos

    def _aplicar_servicos(self, amostra):
        eventos = []
        for servico in amostra.get("servicos") or []:
            self._servicos.inserir(servico["nome"], servico)
            eventos.extend(eventos_do_servico(servico))
        return eventos

    def _registrar(self, evento):
        """
        Insere o evento na árvore. A chave é (timestamp, sequência): dois
        eventos no mesmo instante são chaves diferentes, então nenhum sobrescreve
        o outro, e a ordem da sequência preserva a ordem de chegada.
        """
        self._sequencia += 1
        self._eventos.inserir((evento.timestamp, self._sequencia), evento)
        if evento.severidade in ("alerta", "critico"):
            self._alertas.enfileirar(evento)
        while len(self._eventos) > self._max_eventos:
            self._eventos.remover(self._eventos.minimo())
        self._rebalancear_se_preciso()

    def _rebalancear_se_preciso(self):
        """
        A árvore de eventos tem um problema conhecido: as chaves são horários,
        que chegam quase sempre em ordem crescente. Inserir chaves ordenadas numa
        BST sem balanceamento degenera a árvore numa lista (altura = n), e aí a
        consulta por intervalo volta a custar O(n), perdendo a vantagem sobre
        varrer tudo.

        Em vez de trocar por uma árvore balanceada (AVL/rubro-negra), que fugiria
        do escopo, reconstruímos a árvore quando a altura passa do dobro do
        ideal: a travessia em ordem devolve as chaves ordenadas em O(n) e a
        inserção do meio de cada metade, recursivamente, produz uma árvore de
        altura mínima. O custo é O(n) e acontece raramente (a altura só volta a
        dobrar depois de muitas inserções), então o custo amortizado por evento
        continua O(log n). ``altura_arvore_eventos`` no resumo mostra o efeito.
        """
        total = len(self._eventos)
        if total < 8:
            return
        ideal = total.bit_length()  # ~log2(n) + 1
        if self._eventos.altura() <= 2 * ideal:
            return
        itens = [par for par in self._eventos.em_ordem()]
        nova = ArvoreBusca()
        self._inserir_balanceado(nova, itens, 0, len(itens) - 1)
        self._eventos = nova

    @staticmethod
    def _inserir_balanceado(arvore, itens, inicio, fim):
        """Insere o elemento do meio e depois cada metade: altura mínima."""
        if inicio > fim:
            return
        meio = (inicio + fim) // 2
        chave, valor = itens[meio]
        arvore.inserir(chave, valor)
        EstadoMonitor._inserir_balanceado(arvore, itens, inicio, meio - 1)
        EstadoMonitor._inserir_balanceado(arvore, itens, meio + 1, fim)

    def registrar_evento_proprio(self, evento):
        """Evento gerado pelo próprio monitor (falha de um coletor, por exemplo)."""
        with self._lock:
            self._registrar(evento)

    # ------------------------------------------------------------------ leitura

    def ultima(self, nivel):
        with self._lock:
            janela = self._historico.obter(nivel)
            if janela.vazia():
                return None
            ultima = None
            for amostra in janela:
                ultima = amostra
            return ultima

    def historico(self, nivel, desde=None, limite=None):
        """
        Amostras do nível, da mais antiga para a mais nova. Com ``desde``, usa
        busca binária: a janela já está em ordem crescente de timestamp, então
        achar o ponto de corte custa O(log n) em vez de varrer tudo.
        """
        with self._lock:
            amostras = [a for a in self._historico.obter(nivel)]
        if desde is not None and amostras:
            inicio = self._primeira_a_partir_de(amostras, desde)
            amostras = amostras[inicio:] if inicio >= 0 else []
        if limite:
            amostras = amostras[-limite:]
        return amostras

    @staticmethod
    def _primeira_a_partir_de(amostras, desde):
        """
        Índice da primeira amostra com timestamp >= ``desde``, ou -1 se não há.
        A busca binária acha a igualdade exata; quando não existe, varre o
        resultado para o primeiro maior, o que ainda é O(log n) + O(1) na
        prática porque as amostras são regulares no tempo.
        """
        exato = busca_binaria(amostras, desde, chave=lambda a: a.get("timestamp"))
        if exato >= 0:
            return exato
        inicio, fim = 0, len(amostras) - 1
        resposta = -1
        while inicio <= fim:
            meio = (inicio + fim) // 2
            if amostras[meio].get("timestamp") >= desde:
                resposta = meio
                fim = meio - 1
            else:
                inicio = meio + 1
        return resposta

    def containers(self, ordenar_por="cpu", decrescente=True):
        """Estado atual de cada container, ordenado com o quick sort próprio."""
        chaves = {
            "cpu": lambda c: c.get("cpu_pct") if c.get("cpu_pct") is not None else -1.0,
            "memoria": lambda c: (c.get("memoria") or {}).get("uso_pct") or -1.0,
            "nome": lambda c: c.get("nome") or "",
        }
        with self._lock:
            lista = [valor for _, valor in self._containers.itens()]
        if ordenar_por == "nome":
            return quick_sort(lista, chave=chaves["nome"], decrescente=False)
        return quick_sort(lista, chave=chaves.get(ordenar_por, chaves["cpu"]), decrescente=decrescente)

    def servicos(self):
        with self._lock:
            lista = [valor for _, valor in self._servicos.itens()]
        return quick_sort(lista, chave=lambda s: s.get("nome") or "", decrescente=False)

    def container(self, nome):
        """Acesso direto por chave, O(1) médio: é para isso que a hash está aqui."""
        with self._lock:
            return self._containers.obter(nome, None)

    def processo_por_nome(self, nome):
        """
        Procura um processo na última amostra do nível 1. A amostra está
        ordenada por CPU, não por nome, então a busca é sequencial, O(n).
        """
        amostra = self.ultima(1)
        if not amostra:
            return None
        processos = amostra.get("processos") or []
        indice = busca_sequencial(processos, nome, chave=lambda p: p.get("nome"))
        return processos[indice] if indice >= 0 else None

    def eventos(self, desde=None, ate=None, limite=50, severidade=None):
        """
        Linha do tempo de eventos, do mais recente para o mais antigo.

        Consulta por intervalo na árvore (O(h + k)) e depois ``merge_sort``, que
        é estável: eventos com o mesmo timestamp mantêm a ordem em que
        aconteceram, o que importa justamente quando vários disparam juntos
        (um container morre por OOM e o serviço cai no mesmo segundo).
        """
        minimo = (desde if desde is not None else 0.0, 0)
        maximo = (ate if ate is not None else time.time() + 1, float("inf"))
        with self._lock:
            encontrados = [valor for _, valor in self._eventos.itens_no_intervalo(minimo, maximo)]
        if severidade:
            encontrados = [e for e in encontrados if e.severidade == severidade]
        ordenados = merge_sort(encontrados, chave=lambda e: e.timestamp, decrescente=True)
        return ordenados[:limite] if limite else ordenados

    def alertas_pendentes(self, maximo=20):
        """
        Retira os alertas da fila. Diferente das amostras, que podem ser
        descartadas pela janela, um alerta não pode se perder: por isso é uma
        ``Fila`` sem limite, e não uma ``FilaCircular``.
        """
        with self._lock:
            retirados = []
            while not self._alertas.vazia() and len(retirados) < maximo:
                retirados.append(self._alertas.desenfileirar())
            return retirados

    def resumo(self):
        """Números do próprio monitor, mostrados no topo do painel."""
        with self._lock:
            return {
                "monitor_no_ar_s": round(time.time() - self._inicio, 1),
                "amostras_recebidas": self._amostras_recebidas,
                "eventos_guardados": len(self._eventos),
                "altura_arvore_eventos": self._eventos.altura(),
                "alertas_na_fila": len(self._alertas),
                "janela_amostras": self._janela,
                "containers_conhecidos": len(self._containers),
                "servicos_monitorados": len(self._servicos),
            }
