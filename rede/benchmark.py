"""
Benchmark de rede da loja: latência, vazão, perda e jitter em 3 cenários de uso.

    docker compose run --rm --no-deps -v ./rede:/code/rede -v ./evidencias:/code/evidencias \\
        api python -m rede.benchmark [--cenarios baixo moderado alto] [--repeticoes 3] \\
        [--duracao 20] [--rotulo normal] [--limpar]

Roda DENTRO da rede do Docker Compose (o cliente fala com http://api:8000 como
um proxy reverso falaria), então mede a aplicação + a rede virtual do Docker,
sem a internet do cliente no meio.

Modelo de carga (circuito fechado): N usuários virtuais simultâneos; cada um
repete [escolhe uma ação → espera a resposta → "pensa" um tempo exponencial de
média --pensar] até acabar a duração. Mix de ações tirado do negócio:

    70%  GET  /produtos/{id}          navegação (a api chama recomendacoes por REST)
    20%  GET  /estoque/busca?prefixo= busca no catálogo
    10%  POST /pedidos                compra (checkout com lock no Postgres + fila)

Cenários (usuários simultâneos): baixo = 5 (dia comum), moderado = 25
(campanha/feira), alto = 100 (pico: Natal, Fenearte, Black Friday).

Métricas por rodada:
    vazão     respostas por segundo e kB/s (corpo + cabeçalhos, nível de aplicação)
    latência  p50, p95, p99 e máxima (ms)
    jitter    média de |Δ| entre latências consecutivas do mesmo usuário (ideia da RFC 3550)
    perda     % de requisições sem resposta útil: timeout, erro de conexão ou HTTP 5xx.
              409 (sem estoque) é resposta de negócio, não perda.
"""

import argparse
import asyncio
import csv
import os
import platform
import random
import statistics
import time
from pathlib import Path

import httpx

CENARIOS = {"baixo": 5, "moderado": 25, "alto": 100}
PREFIXOS = ("pe", "peça de barro 1", "peça de cerâmica", "peça de renda 2", "peça de madeira")
CAMPOS = ("rotulo", "cenario", "usuarios", "repeticao", "duracao_s", "requisicoes", "ok", "perdas",
          "perda_pct", "vazao_rps", "vazao_kBps", "p50_ms", "p95_ms", "p99_ms", "max_ms", "jitter_ms",
          "p95_produto_ms", "p95_busca_ms", "p95_compra_ms", "sem_recomendacoes_pct", "erros")


def r1(x):
    return None if x is None else round(x, 1)


def percentil(ordenados, p):
    if not ordenados:
        return None
    return ordenados[min(len(ordenados) - 1, max(0, -(-p * len(ordenados) // 100) - 1))]


async def usuario(cliente, fim, pensar, rnd, registros, n_produtos, erros):
    anterior = None
    jitter = []
    while time.perf_counter() < fim:
        sorteio = rnd.random()
        if sorteio < 0.7:
            acao, req = "produto", cliente.build_request("GET", f"/produtos/{rnd.randint(2, n_produtos + 1)}")
        elif sorteio < 0.9:
            acao, req = "busca", cliente.build_request("GET", "/estoque/busca", params={"prefixo": rnd.choice(PREFIXOS), "limite": 10})
        else:
            acao, req = "compra", cliente.build_request("POST", "/pedidos", json={"produto_id": rnd.randint(2, n_produtos + 1), "cliente_id": rnd.randint(1, 10_000)})
        inicio = time.perf_counter()
        ok, tamanho, sem_rec, erro = False, 0, False, None
        try:
            r = await cliente.send(req)
            ok = r.status_code < 500
            erro = None if ok else f"HTTP {r.status_code}"
            tamanho = len(r.content) + sum(len(k) + len(v) + 4 for k, v in r.headers.items())
            if acao == "produto" and r.status_code == 200:
                sem_rec = not r.json().get("recomendacoes_disponiveis", True)
        except httpx.TimeoutException:
            erro = "timeout"
        except httpx.HTTPError as e:
            erro = type(e).__name__
        ms = (time.perf_counter() - inicio) * 1000
        registros.append((acao, ok, ms, tamanho, sem_rec))
        if erro:
            erros[f"{acao}: {erro}"] = erros.get(f"{acao}: {erro}", 0) + 1
        if ok:
            if anterior is not None:
                jitter.append(abs(ms - anterior))
            anterior = ms
        await asyncio.sleep(rnd.expovariate(1 / pensar) if pensar > 0 else 0)
    return jitter


async def rodada(url, usuarios, duracao, pensar, timeout, semente, n_produtos):
    limites = httpx.Limits(max_connections=usuarios, max_keepalive_connections=usuarios)
    async with httpx.AsyncClient(base_url=url, timeout=timeout, limits=limites) as cliente:
        registros = []
        erros = {}
        inicio = time.perf_counter()
        fim = inicio + duracao
        jitters = await asyncio.gather(*(usuario(cliente, fim, pensar, random.Random(semente * 1000 + i), registros, n_produtos, erros)
                                         for i in range(usuarios)))
        decorrido = time.perf_counter() - inicio
    oks = sorted(ms for _, ok, ms, _, _ in registros if ok)
    por_acao = {a: sorted(ms for acao, ok, ms, _, _ in registros if ok and acao == a) for a in ("produto", "busca", "compra")}
    produtos = [r for r in registros if r[0] == "produto" and r[1]]
    todos_jitter = [j for lista in jitters for j in lista]
    total = len(registros)
    return {
        "requisicoes": total, "ok": len(oks), "perdas": total - len(oks),
        "perda_pct": round(100 * (total - len(oks)) / total, 2) if total else 0,
        "duracao_s": round(decorrido, 1),
        "vazao_rps": round(len(oks) / decorrido, 1),
        "vazao_kBps": round(sum(t for _, ok, _, t, _ in registros if ok) / 1024 / decorrido, 1),
        # Sem nenhuma resposta (100% de perda) não há latência: fica vazio no CSV.
        "p50_ms": r1(percentil(oks, 50)), "p95_ms": r1(percentil(oks, 95)),
        "p99_ms": r1(percentil(oks, 99)), "max_ms": r1(oks[-1] if oks else None),
        "jitter_ms": round(statistics.mean(todos_jitter), 1) if todos_jitter else 0,
        "p95_produto_ms": r1(percentil(por_acao["produto"], 95)),
        "p95_busca_ms": r1(percentil(por_acao["busca"], 95)),
        "p95_compra_ms": r1(percentil(por_acao["compra"], 95)),
        "erros": "; ".join(f"{k} = {v}" for k, v in sorted(erros.items(), key=lambda kv: -kv[1])),
        "sem_recomendacoes_pct": round(100 * sum(1 for r in produtos if r[4]) / len(produtos), 1) if produtos else 0,
    }


def escrever_md(csv_caminho, md_caminho, cabecalho):
    with open(csv_caminho, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    grupos = {}
    for l in linhas:
        grupos.setdefault((l["rotulo"], l["cenario"]), []).append(l)

    def valores(ls, campo):
        return [float(l[campo]) for l in ls if l[campo] not in ("", None)]

    def med(ls, campo):
        vs = valores(ls, campo)
        return f"{statistics.median(vs):g}" if vs else "—"

    def faixa(ls, campo):
        vs = valores(ls, campo)
        if not vs:
            return "—"
        sem = len(ls) - len(vs)
        extra = f"; {sem} rodada(s) sem resposta" if sem else ""
        return f"{statistics.median(vs):g} ({min(vs):g}–{max(vs):g}{extra})"

    with open(md_caminho, "w", encoding="utf-8") as f:
        f.write("# Benchmark de rede (latência, vazão, perda, jitter)\n\n")
        f.write(cabecalho + "\n\n")
        f.write("Mediana das repetições e, entre parênteses, mínimo–máximo.\n\n")
        f.write("| Rede | Cenário | Usuários | Vazão (resp/s) | Vazão (kB/s) | p50 (ms) | p95 (ms) | p99 (ms) | Jitter (ms) | Perda (%) | Sem recomendações (%) |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for (rotulo, cenario), ls in grupos.items():
            f.write(f"| {rotulo} | {cenario} | {ls[0]['usuarios']} | {faixa(ls, 'vazao_rps')} | {med(ls, 'vazao_kBps')} | "
                    f"{med(ls, 'p50_ms')} | {faixa(ls, 'p95_ms')} | {med(ls, 'p99_ms')} | {faixa(ls, 'jitter_ms')} | "
                    f"{faixa(ls, 'perda_pct')} | {med(ls, 'sem_recomendacoes_pct')} |\n")
        f.write("\np95 por ação (mediana das repetições, ms):\n\n| Rede | Cenário | Página do produto | Busca | Compra |\n|---|---|---:|---:|---:|\n")
        for (rotulo, cenario), ls in grupos.items():
            f.write(f"| {rotulo} | {cenario} | {med(ls, 'p95_produto_ms')} | {med(ls, 'p95_busca_ms')} | {med(ls, 'p95_compra_ms')} |\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.getenv("BENCH_URL", "http://api:8000"))
    p.add_argument("--cenarios", nargs="+", default=list(CENARIOS), choices=list(CENARIOS))
    p.add_argument("--repeticoes", type=int, default=3)
    p.add_argument("--duracao", type=float, default=20)
    p.add_argument("--pensar", type=float, default=0.2, help="tempo médio de 'pensar' entre ações (s)")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--produtos", type=int, default=2000)
    p.add_argument("--rotulo", default="normal", help="nome da condição de rede (ex.: normal, degradada)")
    p.add_argument("--saida", default="evidencias/so")
    p.add_argument("--primeira-repeticao", type=int, default=1, help="para completar uma série interrompida")
    p.add_argument("--so-relatorio", action="store_true", help="só refaz o .md a partir do CSV")
    p.add_argument("--limpar", action="store_true", help="apaga o CSV antes (senão acrescenta)")
    a = p.parse_args()

    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    csv_caminho = saida / "benchmark_rede.csv"
    if a.limpar and csv_caminho.exists():
        csv_caminho.unlink()
    novo = not csv_caminho.exists()

    if not a.so_relatorio:
        # A busca usa o estoque em memória da api: garante que o catálogo foi importado.
        httpx.post(f"{a.url}/estoque/importar", timeout=30).raise_for_status()

        with open(csv_caminho, "a", newline="", encoding="utf-8") as f:
            escritor = csv.DictWriter(f, fieldnames=CAMPOS)
            if novo:
                escritor.writeheader()
            for cenario in a.cenarios:
                for rep in range(a.primeira_repeticao, a.repeticoes + 1):
                    r = asyncio.run(rodada(a.url, CENARIOS[cenario], a.duracao, a.pensar, a.timeout, rep, a.produtos))
                    linha = {"rotulo": a.rotulo, "cenario": cenario, "usuarios": CENARIOS[cenario], "repeticao": rep, **r}
                    escritor.writerow(linha)
                    f.flush()
                    print(f"[{a.rotulo}] {cenario} #{rep}: {r['vazao_rps']} resp/s, p95 {r['p95_ms']} ms, "
                          f"jitter {r['jitter_ms']} ms, perda {r['perda_pct']}% {r['erros']}", flush=True)
                    time.sleep(3)  # deixa a fila e o banco assentarem entre rodadas

    cabecalho = (f"Gerado por `python -m rede.benchmark` em {time.strftime('%Y-%m-%d %H:%M')}: cliente assíncrono "
                 f"(httpx) dentro da rede do Docker Compose, {a.duracao:g} s por rodada, {a.repeticoes} repetições, "
                 f"pensar ~ exp(média {a.pensar:g} s), timeout {a.timeout:g} s. Python {platform.python_version()}, "
                 f"os.cpu_count() = {os.cpu_count()} (o cliente divide a máquina com os serviços).")
    escrever_md(csv_caminho, saida / "benchmark_rede.md", cabecalho)
    print((saida / "benchmark_rede.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
