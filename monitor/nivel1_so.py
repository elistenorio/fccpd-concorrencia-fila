"""
Nível 1 — hardware e sistema operacional, lendo ``/proc``.

As funções de parse (``parse_*``) recebem o texto do arquivo e não tocam no
disco, para poderem ser testadas com amostras fixas (ver
``tests/test_monitor_nivel1.py``). Só as funções ``ler_*`` abrem arquivos.

``/proc/stat`` e ``/proc/<pid>/stat`` trazem contadores acumulados desde o boot,
não percentuais: a CPU só faz sentido como diferença entre duas leituras. Por
isso o leitor guarda a leitura anterior (``ColetorSO``) e a primeira amostra sai
sem percentual de CPU.
"""

import os
import time

from estruturas.ordenacao import quick_sort

PROC = "/proc"
TICKS_POR_SEGUNDO = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

# Estados de processo em /proc/<pid>/stat (campo 3).
ESTADOS = {
    "R": "executando",
    "S": "dormindo",
    "D": "espera de disco",
    "Z": "zumbi",
    "T": "parado",
    "t": "em depuração",
    "I": "ocioso",
}


def parse_stat_cpus(texto):
    """
    Linhas ``cpu``/``cpuN`` de ``/proc/stat`` -> lista de (nome, ocupado, total)
    em ticks. ``ocupado = total - idle - iowait``.
    """
    cpus = []
    for linha in texto.splitlines():
        if not linha.startswith("cpu"):
            continue
        partes = linha.split()
        nome = partes[0]
        campos = [int(v) for v in partes[1:]]
        # Ordem: user nice system idle iowait irq softirq steal guest guest_nice
        total = sum(campos)
        idle = campos[3] + (campos[4] if len(campos) > 4 else 0)
        cpus.append((nome, total - idle, total))
    return cpus


def parse_meminfo(texto):
    """``/proc/meminfo`` -> dict em bytes com o que o painel usa."""
    valores = {}
    for linha in texto.splitlines():
        chave, _, resto = linha.partition(":")
        campos = resto.split()
        if not campos:
            continue
        # Os valores vêm em kB (exceto alguns contadores que não usamos).
        valores[chave] = int(campos[0]) * 1024 if len(campos) > 1 else int(campos[0])

    total = valores.get("MemTotal", 0)
    disponivel = valores.get("MemAvailable", valores.get("MemFree", 0))
    swap_total = valores.get("SwapTotal", 0)
    swap_livre = valores.get("SwapFree", 0)
    return {
        "total_bytes": total,
        "disponivel_bytes": disponivel,
        "usada_bytes": total - disponivel,
        "uso_pct": round(100 * (total - disponivel) / total, 1) if total else 0.0,
        "swap_total_bytes": swap_total,
        "swap_usada_bytes": swap_total - swap_livre,
    }


def parse_proc_stat_processo(texto):
    """
    ``/proc/<pid>/stat`` -> dict do processo.

    O nome (campo 2) vem entre parênteses e pode conter espaços e parênteses
    (ex.: ``(postgres: logger)``), então os campos são separados a partir do
    ÚLTIMO ``)``, e não com um split simples.
    """
    inicio_nome = texto.index("(")
    fim_nome = texto.rindex(")")
    pid = int(texto[:inicio_nome].strip())
    nome = texto[inicio_nome + 1 : fim_nome]
    # campos[0] é o estado (campo 3 do arquivo); o deslocamento é -3.
    campos = texto[fim_nome + 2 :].split()
    return {
        "pid": pid,
        "nome": nome,
        "estado": ESTADOS.get(campos[0], campos[0]),
        "ticks_cpu": int(campos[11]) + int(campos[12]),  # utime + stime
        "threads": int(campos[17]),
        "rss_bytes": int(campos[21]) * os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 0,
    }


def parse_uptime(texto):
    """``/proc/uptime`` -> segundos desde o boot."""
    return float(texto.split()[0])


def _ler(caminho):
    with open(caminho, "r", encoding="utf-8", errors="replace") as arquivo:
        return arquivo.read()


def ler_disco(ponto="/"):
    """Uso do disco pela syscall ``statvfs``."""
    if not os.path.isdir(ponto):
        ponto = "/"
    info = os.statvfs(ponto)
    total = info.f_blocks * info.f_frsize
    livre = info.f_bavail * info.f_frsize
    usado = total - livre
    return {
        "ponto": ponto,
        "total_bytes": total,
        "usado_bytes": usado,
        "livre_bytes": livre,
        "uso_pct": round(100 * usado / total, 1) if total else 0.0,
    }


class ColetorSO:
    """
    Produz uma amostra do nível 1 por chamada de :meth:`coletar`.

    Guarda os contadores da leitura anterior para transformar os acumulados de
    ``/proc`` em percentual de CPU. Uma instância é usada por uma única thread
    (a produtora do nível 1), então não precisa de lock.
    """

    def __init__(self, proc=PROC, max_processos=25, ponto_disco=None):
        self._proc = proc
        self._max_processos = max_processos
        # Dentro do container, "/" é o sistema de arquivos da imagem. O compose
        # monta a raiz do host em /hostfs (somente leitura) e aponta para cá,
        # para o painel mostrar o disco da máquina, e não o da imagem.
        self._ponto_disco = ponto_disco or os.getenv("MONITOR_DISCO") or "/"
        self._cpus_anterior = None
        self._processos_anterior = {}
        self._instante_anterior = None

    def coletar(self):
        agora = time.time()
        cpus = parse_stat_cpus(_ler(f"{self._proc}/stat"))
        memoria = parse_meminfo(_ler(f"{self._proc}/meminfo"))
        uptime = parse_uptime(_ler(f"{self._proc}/uptime"))
        processos, total_threads = self._coletar_processos(agora)

        uso_cpus = self._uso_cpu(cpus)
        self._cpus_anterior = cpus
        self._instante_anterior = agora

        return {
            "timestamp": agora,
            "uptime_s": uptime,
            "cpu_total_pct": uso_cpus[0]["uso_pct"] if uso_cpus else None,
            "cpus": uso_cpus[1:],  # por núcleo, sem a linha agregada
            "memoria": memoria,
            "disco": ler_disco(self._ponto_disco),
            "processos": processos,
            "total_processos": len(processos),
            "total_threads": total_threads,
        }

    def _uso_cpu(self, cpus):
        if self._cpus_anterior is None:
            return [{"nome": nome, "uso_pct": None} for nome, _, _ in cpus]
        anteriores = {nome: (ocupado, total) for nome, ocupado, total in self._cpus_anterior}
        resultado = []
        for nome, ocupado, total in cpus:
            if nome not in anteriores:
                resultado.append({"nome": nome, "uso_pct": None})
                continue
            ocupado_antes, total_antes = anteriores[nome]
            delta_total = total - total_antes
            delta_ocupado = ocupado - ocupado_antes
            uso = round(100 * delta_ocupado / delta_total, 1) if delta_total > 0 else 0.0
            resultado.append({"nome": nome, "uso_pct": max(0.0, min(100.0, uso))})
        return resultado

    def _coletar_processos(self, agora):
        """
        Varre ``/proc/<pid>/stat``. Processos nascem e morrem durante a varredura,
        então cada leitura é protegida: um PID que desaparece no meio é ignorado.
        """
        intervalo = (agora - self._instante_anterior) if self._instante_anterior else None
        atuais = {}
        processos = []
        total_threads = 0

        for entrada in os.listdir(self._proc):
            if not entrada.isdigit():
                continue
            try:
                dados = parse_proc_stat_processo(_ler(f"{self._proc}/{entrada}/stat"))
            except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, IndexError):
                continue

            pid = dados["pid"]
            atuais[pid] = dados["ticks_cpu"]
            total_threads += dados["threads"]

            dados["cpu_pct"] = None
            if intervalo and intervalo > 0 and pid in self._processos_anterior:
                delta_ticks = dados["ticks_cpu"] - self._processos_anterior[pid]
                uso = 100 * (delta_ticks / TICKS_POR_SEGUNDO) / intervalo
                dados["cpu_pct"] = round(max(0.0, uso), 1)
            processos.append(dados)

        self._processos_anterior = atuais

        # Guardar ~200 processos a cada amostra, em todas as amostras da janela,
        # ocuparia memória sem necessidade: o painel mostra os maiores. Ordena
        # por CPU com o quick sort próprio e corta. Processos sem percentual
        # (primeira amostra ou recém-criados) vão para o fim.
        processos = quick_sort(processos, chave=lambda p: p["cpu_pct"] if p["cpu_pct"] is not None else -1.0, decrescente=True)
        if self._max_processos:
            processos = processos[: self._max_processos]
        return processos, total_threads
