"""
Gera a pasta evidencias/ com os relatórios de todos os testes (Critério 5).

Roda no host, com os containers de pé:
    python gerar_evidencias.py              # 5 rodadas do teste de concorrência
    python gerar_evidencias.py --rodadas 10

O que ele faz:
1. Registra o ambiente: data, commit testado, versões e containers.
2. Roda o test_concorrencia.py --controle-negativo várias vezes seguidas.
3. Roda o test_fila.py dentro do container da API.
4. Roda o test_resiliencia.py e extrai dos logs dos containers a linha do tempo
   da queda do Redis (Redis, API, reconciliador e workers).
5. Escreve evidencias/RESUMO.md com os números de cada execução.

Os arquivos gerados são sempre recriados, então a pasta corresponde ao código
atual. Sai com código != 0 se algum teste falhar.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from test_concorrencia import ESTOQUE_INICIAL, N_REQUISICOES, PRODUTO_ID, run_sql

RAIZ = Path(__file__).resolve().parent
PASTA = RAIZ / "evidencias"
# Os testes imprimem ✅/❌; sem isso, a saída capturada quebra no Windows.
AMBIENTE_DOS_TESTES = {**os.environ, "PYTHONIOENCODING": "utf-8"}

# Linhas dos logs que contam a história da queda do Redis (além das que citam
# os pedidos criados durante o teste).
COMPRA = '"POST /pedidos'
MARCADORES_RESILIENCIA = [
    "Received SIGTERM",
    "Ready to accept connections",
    "Could not connect to Redis",
    "republicados na fila",
    "Não foi possível reconciliar",
    COMPRA,
]

# Cada serviço imprime a própria data/hora (várias em UTC); como a linha do
# tempo já mostra a hora local na frente, esses prefixos só confundiriam.
LIMPEZAS = [
    (r"^\d{2}:\d{2}:\d{2} ", ""),                                # RQ (workers)
    (r"^\d{4}-\d{2}-\d{2} [\d:,]+ [A-Z]+ ", ""),                 # reconciliador
    (r"^\d+:signal-handler \(\d+\) ", ""),                       # Redis
    (r"^\d+:[A-Z] \d{1,2} \w{3} \d{4} [\d:.]+ [*#.-] ", ""),     # Redis
    (r'^INFO:\s+\S+ - "POST /pedidos HTTP/1\.1" ', "POST /pedidos -> "),  # acesso da API
    (r"^WARNING:\s+", "WARNING: "),
]

LEGENDA_LINHA_DO_TEMPO = """\
Como ler:
- redis "Received SIGTERM"               -> o teste desligou o Redis
- api "POST /pedidos -> 201 Created"      -> uma compra feita e respondida com o Redis fora
- api "Fila indisponível: pedido N"       -> a API desistiu de publicar o job desse pedido; ele
                                             fica "pendente" para o reconciliador
- worker "Could not connect to Redis"     -> os workers perderam a fila e tentam reconectar
- redis "Ready to accept connections"     -> o teste religou o Redis
- reconciliador "republicados na fila"    -> pedidos órfãos devolvidos à fila
- worker "processar_pedido(N)" / "Job OK" -> o pedido foi processado

A API publica o job depois de responder, e o aviso "Fila indisponível" só sai
quando essa tentativa termina. Como a busca DNS do host "redis" leva alguns
segundos, o aviso pode aparecer depois de o Redis já ter voltado; as compras em
si (POST) aconteceram com o Redis fora. Pedidos sem esse aviso foram publicados
pela própria API, porque a tentativa dela terminou com o Redis já de volta.
"""


def executar(*cmd: str) -> tuple[int, str]:
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=RAIZ, env=AMBIENTE_DOS_TESTES,
    )
    return r.returncode, (r.stdout + r.stderr).strip("\r\n")


def salvar(nome: str, conteudo: str):
    (PASTA / nome).write_text(conteudo + "\n", encoding="utf-8")


def achar(padrao: str, texto: str) -> str:
    m = re.search(padrao, texto)
    # "|" quebraria as colunas das tabelas do RESUMO.md
    return m.group(1).replace(" | ", " · ") if m else "?"


def agora_local() -> str:
    return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S (UTC%z)")


def limpar_pasta():
    PASTA.mkdir(exist_ok=True)
    # Só apaga o que este script gera, para não levar junto nada colocado à mão.
    for arquivo in [*PASTA.glob("0*_*.txt"), PASTA / "ambiente.txt", PASTA / "RESUMO.md"]:
        arquivo.unlink(missing_ok=True)


# --- Etapas ---

def registrar_ambiente(inicio: str) -> dict:
    _, commit = executar("git", "rev-parse", "--short", "HEAD")
    _, alteracoes = executar("git", "status", "--porcelain", "--", ".", ":(exclude)evidencias")
    partes = [
        f"Gerado em: {inicio}",
        f"Commit testado: {commit}",
        "Alterações não commitadas: " + ("nenhuma" if not alteracoes else "\n" + alteracoes),
        "",
        "== Versões ==",
        f"Python (host): {sys.version.split()[0]}",
        executar("docker", "--version")[1],
        executar("docker", "compose", "version")[1],
        executar("docker", "compose", "exec", "-T", "postgres", "postgres", "--version")[1],
        executar("docker", "compose", "exec", "-T", "redis", "redis-server", "--version")[1],
        "",
        "== Containers ==",
        executar("docker", "compose", "ps")[1],
    ]
    salvar("ambiente.txt", "\n".join(partes))
    return {"commit": commit, "limpo": not alteracoes}


def rodar_concorrencia(rodada: int) -> dict:
    codigo, saida = executar(sys.executable, "test_concorrencia.py", "--controle-negativo")
    salvar(f"01_concorrencia_rodada_{rodada}.txt", saida)
    controle, _, real = saida.partition("=== RELATÓRIO DE TESTE DE CONCORRÊNCIA ===")
    return {
        "codigo": codigo,
        "sem_lock": achar(r"Pedidos Criados no PostgreSQL: (\d+)", controle),
        "sucessos": achar(r"Sucessos \(201 Created\): (\d+)", real),
        "conflitos": achar(r"Conflitos \(409 Conflict\): (\d+)", real),
        "erros": achar(r"Erros Inesperados: (\d+)", real),
        "estoque": achar(r"Estoque Final no PostgreSQL: (\d+)", real),
        "pedidos": achar(r"Pedidos Criados no PostgreSQL: (\d+)", real),
    }


def rodar_fila() -> dict:
    executar("docker", "compose", "cp", "test_fila.py", "api:/code/test_fila.py")
    codigo, saida = executar("docker", "compose", "exec", "-T", "api", "python", "test_fila.py")
    salvar("02_fila.txt", saida)
    return {
        "codigo": codigo,
        "latencia": achar(r"(mín \d+ ms \| mediana \d+ ms \| máx \d+ ms)", saida),
        "tempo": achar(r"processados pelos workers em ([\d.]+)s", saida),
    }


def ler_carimbo(carimbo: str) -> datetime:
    # O docker usa até 9 casas decimais nos segundos; o Python aceita 6.
    base, _, fracao = carimbo.rstrip("Z").partition(".")
    return datetime.fromisoformat(f"{base}.{(fracao + '000000')[:6]}+00:00")


def extrair_linha_do_tempo(desde: datetime, pedido_ids: list[str]) -> list[str]:
    _, logs = executar(
        "docker", "compose", "logs", "-t", "--no-color",
        "--since", desde.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "redis", "api", "reconciliador", "worker",
    )
    marcadores = (
        MARCADORES_RESILIENCIA
        + [f"pedido {i} " for i in pedido_ids]
        + [f"(pedido-{i})" for i in pedido_ids]
    )
    eventos = []
    for linha in logs.splitlines():
        servico, separador, resto = linha.partition("|")
        carimbo, _, mensagem = resto.strip().partition(" ")
        if not separador or not any(m in mensagem for m in marcadores):
            continue
        try:
            quando = ler_carimbo(carimbo)
        except ValueError:
            continue
        eventos.append((quando, servico.strip(), mensagem.strip()))

    # Das compras, só interessam as feitas depois que o teste derrubou o Redis.
    quedas = [quando for quando, _, mensagem in eventos if "Received SIGTERM" in mensagem]
    eventos = [e for e in eventos if COMPRA not in e[2] or (quedas and e[0] >= quedas[0])]

    for i, (quando, servico, mensagem) in enumerate(eventos):
        for padrao, troca in LIMPEZAS:
            mensagem = re.sub(padrao, troca, mensagem)
        eventos[i] = (quando, servico, mensagem)

    eventos.sort()
    return [
        f"{quando.astimezone():%H:%M:%S}.{quando.microsecond // 1000:03d}  {servico:<16} {mensagem}"
        for quando, servico, mensagem in eventos
    ]


def rodar_resiliencia() -> dict:
    inicio = datetime.now(timezone.utc) - timedelta(seconds=10)
    codigo, saida = executar(sys.executable, "test_resiliencia.py")
    salvar("03_resiliencia.txt", saida)

    # O teste reseta o produto antes de começar, então só sobram os pedidos dele.
    pedido_ids = run_sql(
        f"SELECT id FROM pedidos WHERE produto_id = {PRODUTO_ID} ORDER BY id;", tuples_only=True
    ).split()
    eventos = extrair_linha_do_tempo(inicio, pedido_ids)
    salvar("03_resiliencia_logs.txt", "\n".join([
        "LINHA DO TEMPO DO TESTE DE RESILIÊNCIA (extraída dos logs dos containers)",
        f"Pedidos criados com o Redis fora: {', '.join(pedido_ids)}",
        "Horários no fuso local.",
        "",
        LEGENDA_LINHA_DO_TEMPO,
        *eventos,
    ]))
    return {
        "codigo": codigo,
        "respostas": achar(r"Respostas 201 Created: (\d+)", saida),
        "latencia": achar(r"Latência do POST /pedidos: (mediana \d+ ms \| máx \d+ ms)", saida),
        "estoque": achar(r"Estoque no PostgreSQL: (\d+ -> \d+)", saida),
        "processados": achar(r"Pedidos 'processado': (\d+ de \d+ em [\d.]+s)", saida),
        "eventos": len(eventos),
    }


# --- Resumo ---

def simbolo(execucao: dict) -> str:
    return "✅" if execucao["codigo"] == 0 else "❌"


def somar(rodadas: list[dict], campo: str) -> str:
    valores = [r[campo] for r in rodadas]
    return str(sum(map(int, valores))) if all(v.isdigit() for v in valores) else "?"


def escrever_resumo(inicio: str, duracao_s: float, ambiente: dict,
                    rodadas: list[dict], fila: dict, resiliencia: dict):
    execucoes = [*rodadas, fila, resiliencia]
    aprovadas = sum(1 for e in execucoes if e["codigo"] == 0)
    total = len(execucoes)
    commit = f"`{ambiente['commit']}`" + ("" if ambiente["limpo"] else " ⚠️ com alterações não commitadas")
    minutos, segundos = divmod(round(duracao_s), 60)

    linhas = [
        "# Evidências de teste — Origem",
        "",
        f"Gerado automaticamente por `python gerar_evidencias.py` em {inicio} "
        f"(duração: {minutos}min{segundos:02d}s).  ",
        f"Commit testado: {commit} (detalhes em [ambiente.txt](ambiente.txt)).",
        "",
        f"**Resultado geral: {'✅' if aprovadas == total else '❌'} {aprovadas} de {total} execuções aprovadas.**",
        "",
        f"## 1. Concorrência (Critérios 1 e 2) — {len(rodadas)} rodadas",
        "",
        f"Cada rodada dispara {N_REQUISICOES} compras simultâneas de um produto com "
        f"{ESTOQUE_INICIAL} unidades, primeiro contra uma cópia da API **sem** o lock "
        "(controle negativo) e depois contra a API real.",
        "",
        "| Rodada | Sem lock: pedidos aceitos | Com lock: 201 | 409 | Erros | Estoque final | Pedidos no banco | Resultado |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for n, r in enumerate(rodadas, start=1):
        linhas.append(
            f"| [{n}](01_concorrencia_rodada_{n}.txt) | {r['sem_lock']} de {ESTOQUE_INICIAL} unidades "
            f"| {r['sucessos']} | {r['conflitos']} | {r['erros']} | {r['estoque']} | {r['pedidos']} | {simbolo(r)} |"
        )
    linhas += [
        "",
        f"Com o lock: {len(rodadas) * N_REQUISICOES} compras simultâneas no total, "
        f"{somar(rodadas, 'sucessos')} vendas, {somar(rodadas, 'conflitos')} recusas por falta "
        f"de estoque e {somar(rodadas, 'erros')} erros. Sem o lock, a mesma carga vendeu "
        f"{somar(rodadas, 'sem_lock')} unidades de um estoque total de {len(rodadas) * ESTOQUE_INICIAL}.",
        "",
        "## 2. Fila assíncrona (Critério 3)",
        "",
        "| Latência do POST (job leva 3000 ms) | 5 pedidos processados em | Resultado |",
        "|---|---|---|",
        f"| {fila['latencia']} | {fila['tempo']} s | [{simbolo(fila)}](02_fila.txt) |",
        "",
        "## 3. Resiliência com o Redis fora do ar (Critério 4)",
        "",
        "| Compras com 201 | Latência do POST | Estoque | Processados após religar | Resultado |",
        "|---|---|---|---|---|",
        f"| {resiliencia['respostas']} de 5 | {resiliencia['latencia']} | {resiliencia['estoque']} "
        f"| {resiliencia['processados']} | [{simbolo(resiliencia)}](03_resiliencia.txt) |",
        "",
        f"A [linha do tempo](03_resiliencia_logs.txt) mostra, pelos logs dos próprios containers "
        f"({resiliencia['eventos']} eventos), o Redis caindo, as compras sendo respondidas com "
        "201 mesmo assim, os workers perdendo a conexão, o Redis voltando, o reconciliador "
        "republicando os pedidos órfãos e os workers processando cada um deles.",
        "",
        "## Arquivos",
        "",
        "- [ambiente.txt](ambiente.txt): data, commit, versões e containers",
        *[f"- [01_concorrencia_rodada_{n}.txt](01_concorrencia_rodada_{n}.txt)"
          for n in range(1, len(rodadas) + 1)],
        "- [02_fila.txt](02_fila.txt)",
        "- [03_resiliencia.txt](03_resiliencia.txt)",
        "- [03_resiliencia_logs.txt](03_resiliencia_logs.txt)",
    ]
    salvar("RESUMO.md", "\n".join(linhas))
    return aprovadas, total


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rodadas", type=int, default=5, help="rodadas do teste de concorrência (padrão: 5)")
    args = parser.parse_args()

    os.chdir(RAIZ)  # os testes chamam "docker compose" a partir da pasta do projeto
    try:
        httpx.get("http://localhost:8000/", timeout=3).raise_for_status()
    except httpx.HTTPError:
        sys.exit("A API não respondeu em http://localhost:8000. Suba os containers com: docker compose up -d --build")

    inicio, cronometro = agora_local(), time.monotonic()
    limpar_pasta()
    print("[1/4] Registrando o ambiente...")
    ambiente = registrar_ambiente(inicio)

    rodadas = []
    for n in range(1, args.rodadas + 1):
        print(f"[2/4] Concorrência: rodada {n}/{args.rodadas}...", end=" ", flush=True)
        rodadas.append(rodar_concorrencia(n))
        print("OK" if rodadas[-1]["codigo"] == 0 else "FALHOU")

    print("[3/4] Fila...", end=" ", flush=True)
    fila = rodar_fila()
    print("OK" if fila["codigo"] == 0 else "FALHOU")

    print("[4/4] Resiliência (o Redis fica desligado por alguns segundos)...", end=" ", flush=True)
    resiliencia = rodar_resiliencia()
    print("OK" if resiliencia["codigo"] == 0 else "FALHOU")

    aprovadas, total = escrever_resumo(
        inicio, time.monotonic() - cronometro, ambiente, rodadas, fila, resiliencia
    )
    print(f"\n{aprovadas} de {total} execuções aprovadas. Relatório: evidencias/RESUMO.md")
    if not ambiente["limpo"]:
        print("Aviso: há alterações não commitadas; o RESUMO.md registra isso. "
              "Para evidências de uma versão fechada, faça o commit e gere de novo.")
    if aprovadas != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
