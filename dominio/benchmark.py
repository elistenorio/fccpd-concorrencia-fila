"""
Benchmark do modelo de domínio: cada estrutura contra a alternativa ingênua, em volumes crescentes.

    python -m dominio.benchmark [--volumes 1000 10000 50000] [--repeticoes 5] [--saida evidencias/aed]

Para cada volume n gera um estoque sintético (semente fixa) e mede:

    A  consulta por código     TabelaHash  x  varrer a ListaEncadeada
    B  busca por prefixo       ArvoreBusca.intervalo  x  varrer e filtrar
    C  faixa de preço          busca binária no catálogo  x  varrer e filtrar
       (+ custo de refazer o catálogo com merge_sort depois de uma mudança)
    D  lotes por validade      inserção ordenada: chegando em ordem (atalho O(1)) x aleatória (O(L))
    E  fila de pedidos         FIFO (Fila, O(1)) x prioridade (ListaOrdenada, O(n) por inserção)

Tempo = mediana de ``repeticoes`` rodadas (time.perf_counter), por operação.
"""

import argparse
import os
import platform
import random
import statistics
import time
from datetime import date, timedelta
from pathlib import Path

from dominio import Loja

NOMES = ("vaso", "cesto", "renda", "doce", "bolsa", "colar", "rede", "prato", "tapete", "boneca")
HOJE = date(2026, 11, 26)


def gerar_loja(n, rnd):
    loja = Loja()
    codigos = [None] * n
    for i in range(n):
        codigo = f"P{rnd.randrange(10**9):09d}{i}"
        loja.cadastrar(codigo, f"{rnd.choice(NOMES)} {rnd.randrange(10_000)}", preco_centavos=rnd.randrange(500, 50_000))
        codigos[i] = codigo
    return loja, codigos


def medir(funcao, repeticoes, por=1):
    """Mediana em microssegundos por operação."""
    tempos = [0.0] * repeticoes
    for r in range(repeticoes):
        inicio = time.perf_counter()
        funcao()
        tempos[r] = (time.perf_counter() - inicio) * 1e6 / por
    return statistics.median(tempos)


def rodar(volumes, repeticoes):
    linhas = []
    for n in volumes:
        rnd = random.Random(42 + n)
        loja, codigos = gerar_loja(n, rnd)
        est = loja.estoque
        alvos = [codigos[rnd.randrange(n)] for _ in range(200)]

        hash_us = medir(lambda: [est.consultar(c) for c in alvos], repeticoes, len(alvos))
        seq_us = medir(lambda: [est.consultar_sequencial(c) for c in alvos[:20]], repeticoes, 20)
        linhas.append((n, "A consulta por código", "TabelaHash", hash_us, "varredura da lista", seq_us))

        prefixos = [f"{rnd.choice(NOMES)} {rnd.randrange(100)}" for _ in range(20)]
        abb_us = medir(lambda: [est.buscar_prefixo(p) for p in prefixos], repeticoes, len(prefixos))
        varre_us = medir(lambda: [[x for x in est if x.nome.startswith(p)] for p in prefixos[:5]], repeticoes, 5)
        linhas.append((n, "B busca por prefixo", f"ArvoreBusca (altura {est._por_nome.altura()})", abb_us, "varredura + filtro", varre_us))

        faixas = [(m, m + 500) for m in (rnd.randrange(500, 49_000) for _ in range(50))]
        est.faixa_de_preco(0, 0)  # constrói o catálogo antes
        bin_us = medir(lambda: [est.faixa_de_preco(a, b) for a, b in faixas], repeticoes, len(faixas))
        varre_us = medir(lambda: [[x for x in est if a <= x.preco_centavos <= b] for a, b in faixas[:5]], repeticoes, 5)
        linhas.append((n, "C faixa de preço (±500)", "busca binária", bin_us, "varredura + filtro", varre_us))

        def refazer():
            est._por_preco = None
            est.faixa_de_preco(0, 0)
        linhas.append((n, "C' refazer catálogo", "merge_sort ×2", medir(refazer, repeticoes), "—", None))

        lotes = min(n, 2000)
        dias = [rnd.randrange(-30, 720) for _ in range(lotes)]

        def lotes_em_ordem():
            loja2 = Loja()
            loja2.cadastrar("X", "x")
            for d in sorted(dias):
                loja2.estoque.entrada("X", 1, HOJE + timedelta(days=d))

        def lotes_aleatorios():
            loja2 = Loja()
            loja2.cadastrar("X", "x")
            for d in dias:
                loja2.estoque.entrada("X", 1, HOJE + timedelta(days=d))
        linhas.append((n, f"D {lotes} lotes por validade", "chegando em ordem", medir(lotes_em_ordem, repeticoes, lotes),
                       "ordem aleatória", medir(lotes_aleatorios, repeticoes, lotes)))

        pedidos = min(n, 5000)
        prioridades = [rnd.randrange(1, 6) for _ in range(pedidos)]

        def fila(prioridade):
            def rodada():
                loja2 = Loja(prioridade=prioridade)
                loja2.cadastrar("X", "x")
                for p in prioridades:
                    loja2.registrar_pedido("X", 1, prioridade=p)
                while loja2.pedidos.proximo() is not None:
                    pass
            return rodada
        linhas.append((n, f"E {pedidos} pedidos (entrar + sair)", "Fila FIFO", medir(fila(False), repeticoes, pedidos),
                       "ListaOrdenada (prioridade)", medir(fila(True), repeticoes, pedidos)))
        print(f"n={n} ok", flush=True)
    return linhas


def formatar(us):
    if us is None:
        return "—"
    return f"{us / 1000:.2f} ms" if us >= 1000 else f"{us:.2f} µs"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--volumes", type=int, nargs="+", default=[1000, 10000, 50000])
    p.add_argument("--repeticoes", type=int, default=5)
    p.add_argument("--saida", default="evidencias/aed")
    a = p.parse_args()

    inicio = time.time()
    linhas = rodar(a.volumes, a.repeticoes)
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    with open(saida / "benchmark_dominio.csv", "w", encoding="utf-8") as f:
        f.write("n,teste,estrutura,us_por_op,alternativa,us_por_op_alternativa\n")
        for n, teste, e1, t1, e2, t2 in linhas:
            f.write(f"{n},{teste},{e1},{t1:.3f},{e2},{'' if t2 is None else f'{t2:.3f}'}\n")
    with open(saida / "benchmark_dominio.md", "w", encoding="utf-8") as f:
        f.write("# Benchmark do modelo de domínio (AED)\n\n")
        f.write(f"Gerado por `python -m dominio.benchmark` em {time.strftime('%Y-%m-%d %H:%M')} "
                f"({time.time() - inicio:.0f} s). Python {platform.python_version()}, {platform.system()} "
                f"{platform.machine()}, os.cpu_count() = {os.cpu_count()}. Mediana de {a.repeticoes} rodadas; "
                f"dados sintéticos com semente fixa (42 + n).\n\n")
        f.write("| n | Teste | Estrutura | Tempo por operação | Alternativa | Tempo por operação | Razão |\n")
        f.write("|---:|---|---|---:|---|---:|---:|\n")
        for n, teste, e1, t1, e2, t2 in linhas:
            razao = f"{t2 / t1:,.0f}×".replace(",", ".") if t2 else "—"
            f.write(f"| {n:,} | {teste} | {e1} | {formatar(t1)} | {e2} | {formatar(t2)} | {razao} |\n".replace(",", "."))
    print((saida / "benchmark_dominio.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
