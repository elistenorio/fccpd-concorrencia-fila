"""
Benchmark do cálculo de recomendações: sequencial x threads x processos
(FCCPD U2, critérios 2 e 3).

Roda dentro de um container igual ao do serviço (mesma imagem, mesmos núcleos):
    docker compose run --rm --no-deps -v ./evidencias:/code/evidencias recomendacoes \
        python -m recomendacoes.benchmark
    (--rapido: 2 repetições e volumes menores, só para conferir que funciona)

O QUE é medido: o tempo de recomendacoes.calculo.calcular(), ou seja, o
trecho paralelo, incluindo o custo de criar o pool de threads/processos (é um
custo real de cada recálculo). Fica de fora a leitura do banco e a gravação,
que são iguais nos três modos; os tempos delas aparecem no POST /recalcular.

Métricas:
- tempo (s): mediana das repetições (mais robusta a ruído que a média);
- vazão: comparações de pares por segundo = n·(n-1) / tempo;
- speedup: tempo sequencial / tempo do modo;
- eficiência: speedup / nº de workers (1,0 = ganho perfeito por núcleo).

COMO:
- dados gerados por app.seed.gerar_dados com semente fixa (reproduzível),
  na mesma proporção do seed padrão: clientes = 5·n, pedidos = 40·n;
- 1 execução de aquecimento descartada + N repetições por configuração;
- o resultado de threads e processos é comparado com o sequencial (tem que
  ser idêntico, senão o benchmark falha);
- Experimento A varia o volume (nº de produtos) com 4 workers;
- Experimento B fixa 2000 produtos e varia o nº de workers.

Experimentos de diagnóstico, para explicar ONDE o ganho se perde:
- C, teto do hardware: um laço puro de CPU (sem memória) em 1..N processos.
  Mostra o máximo de speedup que a máquina entrega;
- D, custo fixo: o modo processos com um volume minúsculo. Quase todo o
  tempo é criar o pool (subir interpretadores com spawn) e encerrá-lo;
- E, contenção: o mesmo bloco de 100 produtos medido dentro de cada worker,
  com 1..N workers rodando ao mesmo tempo. Se o bloco fica mais lento com mais
  workers, eles disputam algo compartilhado (cache L3, memória, frequência).

Saída: evidencias/u2/benchmark.md (tabelas) e evidencias/u2/benchmark.csv
(todas as repetições).
"""

import argparse
import csv
import multiprocessing
import os
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from app.seed import gerar_dados
from recomendacoes import calculo
from recomendacoes.calculo import calcular

PASTA = Path("evidencias/u2")
SEMENTE = 42
TAMANHO_BLOCO_CONTENCAO = 100


def _laco_de_cpu(_):
    # Só aritmética em variáveis locais: nada de memória compartilhada.
    s = 0
    for i in range(3_000_000):
        s += i * i
    return s


def _bloco_cronometrado(indice):
    blocos_disponiveis = len(calculo._compradores) // TAMANHO_BLOCO_CONTENCAO
    inicio = (indice % blocos_disponiveis) * TAMANHO_BLOCO_CONTENCAO
    t = time.perf_counter()
    calculo.calcular_bloco(inicio, inicio + TAMANHO_BLOCO_CONTENCAO, 10)
    return time.perf_counter() - t


def experimento_hardware(lista_workers):
    tarefas = max(lista_workers) * 2
    t = time.perf_counter()
    for i in range(tarefas):
        _laco_de_cpu(i)
    sequencial = time.perf_counter() - t
    linhas = [{"workers": 1, "modo": "sequencial", "tempo": sequencial}]
    contexto = multiprocessing.get_context("spawn")
    for w in lista_workers:
        with contexto.Pool(w) as pool:
            pool.map(_laco_de_cpu, range(w))  # sobe os processos antes de medir
            t = time.perf_counter()
            pool.map(_laco_de_cpu, range(tarefas))
            linhas.append({"workers": w, "modo": "processos", "tempo": time.perf_counter() - t})
    for l in linhas:
        l["speedup"] = sequencial / l["tempo"]
        print(f"  C ×{l['workers']} {l['modo']:<10}: {l['tempo']:.3f}s speedup {l['speedup']:.2f}×", flush=True)
    return linhas


def experimento_custo_fixo(lista_workers, repeticoes):
    dados = preparar_dados(8)
    linhas = []
    for w in lista_workers:
        tempos, _ = medir(dados, "processos", w, repeticoes)
        linhas.append({"workers": w, "tempo": statistics.median(tempos)})
        print(f"  D ×{w}: {linhas[-1]['tempo']:.3f}s para criar e encerrar o pool", flush=True)
    return linhas


def experimento_contencao(dados, lista_workers):
    contexto = multiprocessing.get_context("spawn")
    linhas = []
    for w in lista_workers:
        with contexto.Pool(w, initializer=calculo.preparar, initargs=dados) as pool:
            pool.map(_bloco_cronometrado, range(w))  # aquecimento
            tempos = pool.map(_bloco_cronometrado, range(w * 3))
        linhas.append({"workers": w, "tempo": statistics.median(tempos)})
    for l in linhas:
        l["lentidao"] = l["tempo"] / linhas[0]["tempo"]
        print(f"  E ×{l['workers']}: bloco de {TAMANHO_BLOCO_CONTENCAO} produtos em {l['tempo']:.3f}s "
              f"({l['lentidao']:.2f}× o tempo com 1 worker)", flush=True)
    return linhas


def preparar_dados(n):
    produtos, pedidos = gerar_dados(n, n * 5, n * 40, semente=SEMENTE)
    compradores = [set() for _ in produtos]
    for p in pedidos:
        compradores[p["produto"]].add(p["cliente_id"])
    return (
        [frozenset(c) for c in compradores],
        [p["categoria"] for p in produtos],
        [p["artesao"] for p in produtos],
    )


def medir(dados, modo, workers, repeticoes):
    calcular(*dados, modo=modo, workers=workers)  # aquecimento, descartado
    tempos, resultado = [], None
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        resultado = calcular(*dados, modo=modo, workers=workers)
        tempos.append(time.perf_counter() - inicio)
    return tempos, resultado


def ambiente():
    modelo = "?"
    try:
        for linha in Path("/proc/cpuinfo").read_text().splitlines():
            if linha.startswith("model name"):
                modelo = linha.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    memoria = "?"
    try:
        for linha in Path("/proc/meminfo").read_text().splitlines():
            if linha.startswith("MemTotal"):
                memoria = f"{int(linha.split()[1]) / 1024 / 1024:.1f} GB"
                break
    except OSError:
        pass
    return {
        "data": datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S (UTC%z)"),
        "cpu": modelo,
        "cpus": os.cpu_count(),
        "memoria": memoria,
        "python": platform.python_version(),
        "sistema": f"{platform.system()} {platform.release()}",
    }


class Registro:
    def __init__(self):
        self.linhas = []      # uma por configuração (para as tabelas)
        self.brutos = []      # uma por repetição (para o CSV)
        self.divergencias = []

    def rodar(self, experimento, n, dados, modo, workers, repeticoes, referencia=None):
        tempos, resultado = medir(dados, modo, workers, repeticoes)
        if referencia is not None and resultado != referencia:
            self.divergencias.append(f"{experimento}: {modo}×{workers} com {n} produtos difere do sequencial")
        mediana = statistics.median(tempos)
        linha = {
            "experimento": experimento, "produtos": n, "modo": modo, "workers": workers,
            "mediana_s": mediana, "min_s": min(tempos), "max_s": max(tempos),
            "desvio_s": statistics.stdev(tempos) if len(tempos) > 1 else 0.0,
            "vazao": n * (n - 1) / mediana,
        }
        self.linhas.append(linha)
        for i, t in enumerate(tempos, 1):
            self.brutos.append({"experimento": experimento, "produtos": n, "modo": modo,
                                "workers": workers, "repeticao": i, "tempo_s": round(t, 4)})
        print(f"  {experimento} n={n:<5} {modo:<10} ×{workers}: mediana {mediana:7.3f}s "
              f"(min {min(tempos):.3f} max {max(tempos):.3f})", flush=True)
        return resultado, mediana


def tabela(linhas, base_por_n):
    cab = "| Produtos | Comparações | Modo | Workers | Tempo mediano (s) | Mín–máx (s) | Desvio (s) | Vazão (comparações/s) | Speedup | Eficiência |"
    sep = "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|"
    corpo = []
    for l in linhas:
        speedup = base_por_n[l["produtos"]] / l["mediana_s"]
        corpo.append(
            f"| {l['produtos']} | {l['produtos'] * (l['produtos'] - 1):,} | {l['modo']} | {l['workers']} "
            f"| {l['mediana_s']:.3f} | {l['min_s']:.3f}–{l['max_s']:.3f} | {l['desvio_s']:.3f} "
            f"| {l['vazao']:,.0f} | {speedup:.2f}× | {speedup / l['workers']:.2f} |"
        )
    return "\n".join([cab, sep, *corpo]).replace(",", ".")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeticoes", type=int, default=5)
    parser.add_argument("--rapido", action="store_true")
    args = parser.parse_args()

    cpus = os.cpu_count() or 1
    repeticoes = 2 if args.rapido else args.repeticoes
    volumes = [250, 500, 1000] if args.rapido else [250, 500, 1000, 2000, 4000]
    n_fixo = 1000 if args.rapido else 2000
    workers_a = min(4, cpus)
    lista_workers = sorted({w for w in (1, 2, 4, 6, 8) if w <= cpus} | {cpus})
    lista_threads = [w for w in (2, 4, 8) if w <= cpus]

    amb = ambiente()
    print(f"Ambiente: {amb}")
    reg = Registro()
    base = {}

    print(f"\nExperimento A — volume ({repeticoes} repetições + 1 aquecimento)")
    for n in volumes:
        dados = preparar_dados(n)
        referencia, base[n] = reg.rodar("A", n, dados, "sequencial", 1, repeticoes)
        reg.rodar("A", n, dados, "threads", workers_a, repeticoes, referencia)
        reg.rodar("A", n, dados, "processos", workers_a, repeticoes, referencia)

    print(f"\nExperimento B — nº de workers com {n_fixo} produtos")
    dados = preparar_dados(n_fixo)
    referencia, base_b = reg.rodar("B", n_fixo, dados, "sequencial", 1, repeticoes)
    for w in lista_threads:
        reg.rodar("B", n_fixo, dados, "threads", w, repeticoes, referencia)
    for w in lista_workers:
        reg.rodar("B", n_fixo, dados, "processos", w, repeticoes, referencia)

    print("\nExperimento C — teto do hardware (laço puro de CPU)")
    hardware = experimento_hardware(lista_workers)
    print("\nExperimento D — custo fixo de criar o pool de processos")
    custo_fixo = experimento_custo_fixo(lista_workers, repeticoes)
    print(f"\nExperimento E — contenção entre workers ({n_fixo} produtos)")
    contencao = experimento_contencao(dados, lista_workers)

    tabela_c = "\n".join(
        ["| Workers | Tempo (s) | Speedup |", "|---:|---:|---:|"]
        + [f"| {l['workers']} ({l['modo']}) | {l['tempo']:.3f} | {l['speedup']:.2f}× |" for l in hardware]
    )
    tabela_d = "\n".join(
        ["| Workers | Tempo para criar e encerrar o pool (s) |", "|---:|---:|"]
        + [f"| {l['workers']} | {l['tempo']:.3f} |" for l in custo_fixo]
    )
    tabela_e = "\n".join(
        [f"| Workers simultâneos | Tempo de um bloco de {TAMANHO_BLOCO_CONTENCAO} produtos (s) | Em relação a 1 worker |",
         "|---:|---:|---:|"]
        + [f"| {l['workers']} | {l['tempo']:.3f} | {l['lentidao']:.2f}× |" for l in contencao]
    )

    linhas_a = [l for l in reg.linhas if l["experimento"] == "A"]
    linhas_b = [l for l in reg.linhas if l["experimento"] == "B"]
    relatorio = f"""# Benchmark do cálculo de recomendações

Gerado por `python -m recomendacoes.benchmark` (ver docstring do script para a metodologia completa).

## Ambiente
| Item | Valor |
|---|---|
| Data | {amb['data']} |
| CPU | {amb['cpu']} |
| Núcleos visíveis no container | {amb['cpus']} |
| Memória visível no container | {amb['memoria']} |
| Python | {amb['python']} |
| Sistema (container) | {amb['sistema']} |

## Metodologia
- **Medido:** tempo de `recomendacoes.calculo.calcular()` (o trecho paralelo), incluindo a criação do pool de threads/processos. Leitura do banco e gravação ficam de fora (iguais nos três modos).
- **Dados:** `app.seed.gerar_dados` com semente {SEMENTE}; clientes = 5·n, pedidos = 40·n; n² − n comparações de pares.
- **Repetições:** 1 aquecimento descartado + {repeticoes} medições por configuração; reporta-se a mediana, mínimo, máximo e desvio padrão.
- **Correção:** o resultado de threads e processos foi comparado com o sequencial em todas as configurações: {"idêntico em todas" if not reg.divergencias else "DIVERGÊNCIAS: " + "; ".join(reg.divergencias)}.
- **Speedup** = tempo sequencial / tempo do modo; **eficiência** = speedup / workers.

## Experimento A — efeito do volume ({workers_a} workers)
{tabela(linhas_a, base)}

## Experimento B — efeito do nº de workers ({n_fixo} produtos)
{tabela(linhas_b, {n_fixo: base_b})}

## Diagnóstico — por que o ganho não é linear

### C — teto do hardware (laço puro de CPU, {max(lista_workers) * 2} tarefas iguais)
{tabela_c}

### D — custo fixo de criar e encerrar o pool de processos (8 produtos, cálculo desprezível)
{tabela_d}

### E — contenção: o mesmo trabalho fica mais lento quando há mais workers ao mesmo tempo
{tabela_e}
"""
    PASTA.mkdir(parents=True, exist_ok=True)
    (PASTA / "benchmark.md").write_text(relatorio, encoding="utf-8")
    with open(PASTA / "benchmark.csv", "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=["experimento", "produtos", "modo", "workers", "repeticao", "tempo_s"])
        escritor.writeheader()
        escritor.writerows(reg.brutos)
    print(f"\nSalvo em {PASTA / 'benchmark.md'} e {PASTA / 'benchmark.csv'}")
    if reg.divergencias:
        print("ERRO: resultados paralelos diferentes do sequencial:", *reg.divergencias, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
