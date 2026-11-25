"""
Coletor do monitor: produtor-consumidor com threads lendo o /proc.

    produtor "sistema"   lê /proc/stat e /proc/meminfo        ┐
                                                              ├─> BufferLimitado ─> consumidor
    produtor "processos" percorre /proc/<pid>/stat e status   ┘   (FilaCircular +    calcula % de CPU
                                                                   Lock + 2 Condition) e grava o estado

O consumidor é o único que escreve no estado; a API só lê. Os dois lados usam
``_lock_estado``, porque as estruturas próprias (TabelaHash, ArvoreBusca...)
não são thread-safe. Onde cada estrutura de AED entra:

| Estrutura          | Uso                                                         |
|--------------------|-------------------------------------------------------------|
| FilaCircular       | buffer do produtor-consumidor e histórico das últimas N amostras |
| TabelaHash         | estado por PID e histórico por serviço                      |
| ArvoreBusca        | eventos por (timestamp, seq), consulta por intervalo        |
| Fila               | feed dos eventos mais recentes                              |
| ListaEncadeada     | lista de processos de uma varredura (tamanho desconhecido)  |
| quick/merge sort   | tabela de processos por CPU, memória (quick) ou nome (merge) |
| TopK               | os 5 processos que mais usam CPU                            |
| busca binária      | polling incremental: acha no histórico a última amostra que o navegador já tem |

Regra de AED: o estado não usa dict/list prontos. Cada registro é uma classe
com __slots__ (como os nós de estruturas/) e as coleções são as próprias;
dicionário só aparece em para_json(), na borda com o JSON da API.

Pausar o consumidor (pela API) faz o buffer encher e os produtores dormirem em
``_nao_cheio``: é o produtor-consumidor acontecendo na frente de quem assiste.
"""

import os
import threading
import time

from concorrencia.buffer import BufferLimitado
from estruturas import (
    ArvoreBusca,
    Fila,
    FilaCircular,
    ListaEncadeada,
    TabelaHash,
    TopK,
    busca_binaria,
    merge_sort,
    quick_sort,
)

PROC = "/proc"
NUCLEOS = os.cpu_count() or 1

# Trecho da linha de comando -> serviço do docker-compose. Vale o primeiro que bater.
SERVICOS = (
    ("recomendacoes.main", "recomendacoes"),
    ("app.main", "api"),
    ("monitor.servidor", "monitor"),
    ("-m monitor", "monitor"),
    ("app.reconciliador", "reconciliador"),
    ("app.worker", "worker"),
    ("rq:worker", "worker"),
    ("redis-server", "redis"),
    ("postgres", "postgres"),
)

MAX_EVENTOS = 500
TAMANHO_FEED = 40
TAMANHO_HISTORICO = 120


def _ler(caminho):
    with open(caminho, "rb") as f:
        return f.read().decode("utf-8", "replace")


def _ler_cpu_total():
    """(total, ocioso) em ticks, somando todos os núcleos (1ª linha do /proc/stat)."""
    campos = _ler(f"{PROC}/stat").split("\n", 1)[0].split()[1:]
    total = 0
    for campo in campos[:8]:  # user nice system idle iowait irq softirq steal
        total += int(campo)
    ocioso = int(campos[3]) + int(campos[4])
    return total, ocioso


def _ler_memoria():
    """(total_kb, disponivel_kb) do /proc/meminfo."""
    total = disponivel = 0
    for linha in _ler(f"{PROC}/meminfo").split("\n"):
        if linha.startswith("MemTotal:"):
            total = int(linha.split()[1])
        elif linha.startswith("MemAvailable:"):
            disponivel = int(linha.split()[1])
    return total, disponivel


def _classificar(linha_comando):
    for trecho, servico in SERVICOS:
        if trecho in linha_comando:
            return servico
    return None


class Processo:
    __slots__ = ("pid", "nome", "comando", "servico", "estado", "ticks", "rss_kb", "threads", "cpu")

    def __init__(self, pid, nome, comando, servico, estado, ticks, rss_kb, threads):
        self.pid = pid
        self.nome = nome
        self.comando = comando
        self.servico = servico
        self.estado = estado
        self.ticks = ticks  # utime + stime acumulados; o % de CPU sai da diferença entre varreduras
        self.rss_kb = rss_kb
        self.threads = threads
        self.cpu = 0.0

    def para_json(self):
        return {"pid": self.pid, "nome": self.nome, "comando": self.comando, "servico": self.servico,
                "estado": self.estado, "rss_kb": self.rss_kb, "threads": self.threads, "cpu": self.cpu}


class AmostraSistema:
    __slots__ = ("ts", "cpu", "mem", "mem_total_kb")

    def __init__(self, ts, cpu, mem, mem_total_kb):
        self.ts = ts
        self.cpu = cpu
        self.mem = mem
        self.mem_total_kb = mem_total_kb

    def para_json(self):
        return {"ts": self.ts, "cpu": self.cpu, "mem": self.mem, "mem_total_kb": self.mem_total_kb}


class Evento:
    __slots__ = ("ts", "seq", "tipo", "texto")

    def __init__(self, ts, seq, tipo, texto):
        self.ts = ts
        self.seq = seq
        self.tipo = tipo
        self.texto = texto

    def para_json(self):
        return {"ts": self.ts, "seq": self.seq, "tipo": self.tipo, "texto": self.texto}


class SomaServico:
    """Acumulador de uma varredura: CPU, memória e nº de processos de um serviço."""

    __slots__ = ("cpu", "rss_kb", "processos")

    def __init__(self):
        self.cpu = 0.0
        self.rss_kb = 0
        self.processos = 0


def _ler_processo(pid):
    """Processo lido do /proc, ou None se ele sumiu no meio da leitura."""
    try:
        stat = _ler(f"{PROC}/{pid}/stat")
        status = _ler(f"{PROC}/{pid}/status")
        comando = _ler(f"{PROC}/{pid}/cmdline").replace("\0", " ").strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # O nome (2º campo) vem entre parênteses e pode ter espaços: corta pelo último ')'.
    fecha = stat.rfind(")")
    nome = stat[stat.find("(") + 1 : fecha]
    campos = stat[fecha + 2 :].split()
    estado = campos[0]
    ticks = int(campos[11]) + int(campos[12])  # utime + stime
    rss_kb = threads = 0
    for linha in status.split("\n"):
        if linha.startswith("VmRSS:"):
            rss_kb = int(linha.split()[1])
        elif linha.startswith("Threads:"):
            threads = int(linha.split()[1])
    return Processo(pid, nome, comando[:160] or f"[{nome}]", _classificar(comando), estado, ticks, rss_kb, threads)


class Coletor:
    def __init__(self, intervalo=1.0, capacidade_buffer=8, limite_cpu=80.0):
        self.disponivel = os.path.exists(f"{PROC}/stat")
        self.intervalo = intervalo
        self.limite_cpu = limite_cpu
        self.buffer = BufferLimitado(capacidade_buffer)

        self._lock_estado = threading.Lock()
        self._parar = threading.Event()
        self._retomar = threading.Event()  # limpo = consumidor pausado
        self._retomar.set()
        self._threads = ListaEncadeada()

        # --- estado (só o consumidor escreve; tudo sob _lock_estado) ---
        self.processos = TabelaHash()  # pid -> Processo
        self.historico = FilaCircular(TAMANHO_HISTORICO, sobrescrever=True)  # de AmostraSistema
        self.historico_servicos = TabelaHash()  # serviço -> FilaCircular de (ts, cpu, rss_kb, processos)
        self.eventos = ArvoreBusca()  # (ts, seq) -> Evento
        self.feed = Fila()  # de Evento
        self.situacao_threads = TabelaHash()  # nome da thread -> (estado, desde)
        self.contadores = TabelaHash()
        for nome in ("produzidas", "consumidas", "bloqueios", "eventos"):
            self.contadores[nome] = 0
        self._seq = 0
        self._ultimo_sistema = None  # (total, ocioso) da amostra anterior
        self._ultimo_total_processos = None
        self._primeira_varredura = True

    # ------------------------------------------------------------------ threads

    def iniciar(self):
        if not self.disponivel or not self._threads.vazia():
            return
        alvos = (
            ("produtor-sistema", self._produtor, (self._amostra_sistema,)),
            ("produtor-processos", self._produtor, (self._amostra_processos,)),
            ("consumidor", self._consumidor, ()),
        )
        for nome, alvo, args in alvos:
            self._marcar(nome, "iniciando")
            t = threading.Thread(target=alvo, args=(nome, *args), name=nome, daemon=True)
            self._threads.inserir_fim(t)
            t.start()

    def parar(self):
        self._parar.set()
        self._retomar.set()
        for t in self._threads:
            t.join(timeout=2)

    def pausar_consumidor(self, pausado: bool):
        if pausado:
            self._retomar.clear()
        else:
            self._retomar.set()
        self._evento("controle", "Consumidor pausado" if pausado else "Consumidor retomado")

    def _marcar(self, thread, estado):
        with self._lock_estado:
            self.situacao_threads[thread] = (estado, time.time())

    def _somar(self, contador, valor=1):
        with self._lock_estado:
            self.contadores[contador] = self.contadores[contador] + valor

    def _produtor(self, nome, ler_amostra):
        while not self._parar.is_set():
            self._marcar(nome, "lendo /proc")
            amostra = ler_amostra()
            self._marcar(nome, "colocando no buffer")
            bloqueado = False
            while not self._parar.is_set():
                try:
                    self.buffer.colocar(amostra, timeout=0.25)
                    break
                except TimeoutError:
                    # Continua dormindo dentro do colocar(); o timeout só existe
                    # para atualizar o painel e conseguir encerrar a thread.
                    if not bloqueado:
                        bloqueado = True
                        self._somar("bloqueios")
                        self._marcar(nome, "bloqueado: buffer cheio")
                        self._evento("buffer", f"{nome} dormindo em _nao_cheio (buffer cheio)")
            self._somar("produzidas")
            self._marcar(nome, "dormindo")
            self._parar.wait(self.intervalo)

    def _consumidor(self, nome):
        while not self._parar.is_set():
            if not self._retomar.is_set():
                self._marcar(nome, "pausado")
                self._retomar.wait()
                continue
            self._marcar(nome, "esperando item")
            try:
                amostra = self.buffer.retirar(timeout=0.25)
            except TimeoutError:
                self._marcar(nome, "dormindo: buffer vazio")
                continue
            self._marcar(nome, "processando")
            if amostra[0] == "sistema":
                self._consumir_sistema(*amostra[1:])
            else:
                self._consumir_processos(*amostra[1:])
            self._somar("consumidas")

    # ---------------------------------------------------------------- produção

    def _amostra_sistema(self):
        total, ocioso = _ler_cpu_total()
        mem_total, mem_disp = _ler_memoria()
        return ("sistema", time.time(), total, ocioso, mem_total, mem_disp)

    def _amostra_processos(self):
        total, _ = _ler_cpu_total()
        lista = ListaEncadeada()
        for entrada in os.listdir(PROC):
            if entrada.isdigit():
                processo = _ler_processo(int(entrada))
                if processo is not None:
                    lista.inserir_fim(processo)
        return ("processos", time.time(), total, lista.para_array())

    # ---------------------------------------------------------------- consumo

    def _consumir_sistema(self, ts, total, ocioso, mem_total, mem_disp):
        anterior = self._ultimo_sistema
        self._ultimo_sistema = (total, ocioso)
        if anterior is None or total == anterior[0]:
            return
        cpu = 100.0 * (1 - (ocioso - anterior[1]) / (total - anterior[0]))
        mem = 100.0 * (1 - mem_disp / mem_total) if mem_total else 0.0
        with self._lock_estado:
            self.historico.enfileirar(AmostraSistema(ts, round(cpu, 1), round(mem, 1), mem_total))

    def _consumir_processos(self, ts, total, processos):
        delta_total = None if self._ultimo_total_processos is None else total - self._ultimo_total_processos
        self._ultimo_total_processos = total
        novos = TabelaHash(capacidade_inicial=max(16, len(processos) * 2))
        eventos = ListaEncadeada()
        por_servico = TabelaHash()

        with self._lock_estado:
            for p in processos:
                antigo = self.processos.obter(p.pid, None)
                if antigo is not None and delta_total:
                    # % de um núcleo, como no top: 100% = um núcleo inteiro ocupado.
                    p.cpu = round(100.0 * NUCLEOS * (p.ticks - antigo.ticks) / delta_total, 1)
                    if p.cpu >= self.limite_cpu > antigo.cpu:
                        eventos.inserir_fim(("alerta", f"{p.nome} (PID {p.pid}) passou de {self.limite_cpu:.0f}% de CPU: {p.cpu}%"))
                elif antigo is None and not self._primeira_varredura and p.servico:
                    eventos.inserir_fim(("inicio", f"{p.servico}: processo {p.nome} iniciou (PID {p.pid})"))
                novos[p.pid] = p
                if p.servico:
                    soma = por_servico.obter(p.servico, None)
                    if soma is None:
                        soma = SomaServico()
                        por_servico[p.servico] = soma
                    soma.cpu += p.cpu
                    soma.rss_kb += p.rss_kb
                    soma.processos += 1

            if not self._primeira_varredura:
                for pid, antigo in self.processos.itens():
                    if pid not in novos and antigo.servico:
                        eventos.inserir_fim(("fim", f"{antigo.servico}: processo {antigo.nome} terminou (PID {pid})"))

            self.processos = novos
            for servico, soma in por_servico.itens():
                janela = self.historico_servicos.obter(servico, None)
                if janela is None:
                    janela = FilaCircular(TAMANHO_HISTORICO, sobrescrever=True)
                    self.historico_servicos[servico] = janela
                janela.enfileirar((ts, round(soma.cpu, 1), soma.rss_kb, soma.processos))
            # Serviço sem nenhum processo vivo (container parado) sai do painel.
            sumiram = ListaEncadeada()
            for servico in self.historico_servicos.chaves():
                if servico not in por_servico:
                    sumiram.inserir_fim(servico)
            for servico in sumiram:
                self.historico_servicos.remover(servico)
            self._primeira_varredura = False

        for tipo, texto in eventos:
            self._evento(tipo, texto)

    def _evento(self, tipo, texto):
        with self._lock_estado:
            ts = time.time()
            self._seq += 1
            evento = Evento(ts, self._seq, tipo, texto)
            self.eventos.inserir((ts, self._seq), evento)
            if len(self.eventos) > MAX_EVENTOS:
                self.eventos.remover(self.eventos.minimo())
            self.feed.enfileirar(evento)
            if len(self.feed) > TAMANHO_FEED:
                self.feed.desenfileirar()
            self.contadores["eventos"] = self.contadores["eventos"] + 1

    # ---------------------------------------------------------------- leitura (API)

    def instantaneo(self, desde=None, ordem="cpu", limite=30, janela_eventos=300.0):
        """Tudo que o painel precisa numa resposta. ``desde`` = ts da última amostra que o cliente tem."""
        posicoes, inicio_buffer, ocupacao = self.buffer.retrato()
        agora = time.time()
        with self._lock_estado:
            historico = self._array(self.historico)
            novas = historico
            if desde is not None:
                # O cliente manda o ts exato de uma amostra que recebeu antes, então
                # a busca é por igualdade. -1 = saiu da janela: manda tudo de novo.
                i = busca_binaria(historico, desde, chave=lambda a: a.ts)
                if i >= 0:
                    novas = self._fatia(historico, i + 1)

            processos = self._array_valores(self.processos)
            top = TopK(5)
            for p in processos:
                top.oferecer(p, p.cpu)

            # (nome, pontos) ordenados por nome; o último ponto é a varredura atual.
            servicos = merge_sort(
                ((servico, self._array(janela)) for servico, janela in self.historico_servicos.itens()),
                chave=lambda par: par[0],
            )

            eventos = ListaEncadeada()
            for _, ev in self.eventos.intervalo((agora - janela_eventos, 0), (agora, float("inf"))):
                eventos.inserir_fim(ev)

            resposta = {
                "disponivel": self.disponivel,
                "agora": agora,
                "nucleos": NUCLEOS,
                "intervalo": self.intervalo,
                "limite_cpu": self.limite_cpu,
                "pausado": not self._retomar.is_set(),
                "historico": [a.para_json() for a in novas],
                "historico_completo": desde is None or len(novas) == len(historico),
                "top": [{"pid": p.pid, "nome": p.nome, "cpu": s} for p, s in top.resultado()],
                "servicos": [{"servico": nome, "cpu": pontos[-1][1], "rss_kb": pontos[-1][2], "processos": pontos[-1][3],
                              "serie": [c for _, c, _, _ in pontos]} for nome, pontos in servicos],
                "eventos": [ev.para_json() for ev in eventos],
                "feed": [ev.para_json() for ev in self.feed],
                "threads": [{"nome": n, "estado": e, "desde": round(agora - d, 1)}
                            for n, (e, d) in self._ordenar_itens(self.situacao_threads)],
                "contadores": {n: v for n, v in self.contadores.itens()},
                "estruturas": {
                    "processos_tabela": {"itens": len(self.processos), "baldes": self.processos.capacidade},
                    "eventos_arvore": {"itens": len(self.eventos), "altura": self.eventos.altura()},
                    "historico_fila": {"itens": len(self.historico), "capacidade": self.historico.capacidade},
                    "feed_fila": {"itens": len(self.feed)},
                },
            }

        ordenados = self._ordenar_processos(processos, ordem)
        resposta["total_processos"] = len(ordenados)
        resposta["processos"] = [p.para_json() for p in self._fatia(ordenados, 0, limite)]
        resposta["buffer"] = {
            "capacidade": len(posicoes),
            "inicio": inicio_buffer,
            "ocupacao": ocupacao,
            "posicoes": [None if a is None else {"tipo": a[0], "ts": a[1]} for a in posicoes],
        }
        return resposta

    @staticmethod
    def _ordenar_processos(processos, ordem):
        if ordem == "nome":
            # Merge sort é estável: empates no nome continuam na ordem de PID.
            por_pid = quick_sort(processos, chave=lambda p: p.pid)
            return merge_sort(por_pid, chave=lambda p: p.nome.lower())
        if ordem == "pid":
            return quick_sort(processos, chave=lambda p: p.pid)
        if ordem == "mem":
            return quick_sort(processos, chave=lambda p: (p.rss_kb, p.cpu), decrescente=True)
        return quick_sort(processos, chave=lambda p: (p.cpu, p.rss_kb), decrescente=True)

    @staticmethod
    def _array(iteravel):
        lista = ListaEncadeada()
        for item in iteravel:
            lista.inserir_fim(item)
        return lista.para_array()

    def _array_valores(self, tabela):
        lista = ListaEncadeada()
        for _, valor in tabela.itens():
            lista.inserir_fim(valor)
        return lista.para_array()

    def _ordenar_itens(self, tabela):
        return merge_sort(tabela.itens(), chave=lambda par: par[0])

    @staticmethod
    def _fatia(array, inicio, fim=None):
        fim = len(array) if fim is None else min(fim, len(array))
        resultado = [None] * max(0, fim - inicio)
        for i in range(inicio, fim):
            resultado[i - inicio] = array[i]
        return resultado
