"""
Teste de carga do Origem (PI3, etapa 6: a parte de "carga" da demonstração).

Diferente do `test_concorrencia.py`, que dispara 50 requisições de uma vez para
provar correção, aqui a carga é **sustentada e com taxa controlada**: o script
mantém N requisições por segundo durante alguns minutos e mede como o sistema se
comporta, enquanto o painel do monitor mostra o efeito em tempo real.

Rode com os containers de pé e o painel aberto em http://localhost:8002/painel/

    python teste_carga.py                          # 20 req/s por 30 s, pedidos espalhados
    python teste_carga.py --taxa 40 --duracao 60
    python teste_carga.py --modo disputa           # todos no mesmo produto

**Taxa fixa (open loop), e não "N clientes em laço".** Se o script esperasse
cada resposta para mandar a próxima, a carga cairia junto com o sistema quando
ele ficasse lento, e a lentidão apareceria menor do que é (é o problema conhecido
como *coordinated omission*). Aqui as requisições saem no horário marcado,
respondam ou não.

**Dois modos, que medem coisas diferentes:**

- `espalhado` (padrão): os pedidos são distribuídos entre vários produtos. Mede
  a vazão real do checkout, com os locks caindo em linhas diferentes do banco.
- `disputa`: todos os pedidos vão para o mesmo produto. Como o `POST /pedidos`
  usa `SELECT ... FOR UPDATE`, as transações passam a se enfileirar na mesma
  linha, e a latência mostra o custo do lock pessimista sob carga. É o mesmo
  mecanismo do teste de concorrência, agora medido em vez de só verificado.

**O que esperar da fila.** Cada job do worker simula 3 s de trabalho e são 3
réplicas, então a fila processa ~1 pedido/s, enquanto a api aceita dezenas por
segundo. Isso não é um defeito: é exatamente o desacoplamento da Parte 1, a
compra não espera o pós-venda. O relatório mostra a fila acumulando durante a
carga e a velocidade com que ela drena depois.

Gera evidencias/pi3/carga.txt e sai com código != 0 se a taxa de erro passar do
limite ou se a fila não estiver drenando.
"""

import argparse
import asyncio
import math
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from estruturas.ordenacao import quick_sort

RAIZ = Path(__file__).resolve().parent
SAIDA = RAIZ / "evidencias" / "pi3" / "carga.txt"
# 127.0.0.1 e não "localhost": no Windows, "localhost" tenta IPv6 primeiro e
# cada requisição ganha ~0,5 s que não é da api (mesmo motivo do
# evidencia_comunicacao.py).
API = "http://127.0.0.1:8000"
MONITOR = "http://127.0.0.1:8002"
POSTGRES_SERVICE = "postgres"
DB_USER = "fccpd"
DB_NAME = "fccpd_db"

# Produto usado no modo "disputa" (o mesmo dos outros testes).
PRODUTO_DISPUTA = 1
# Faixa de produtos usada no modo "espalhado".
PRIMEIRO_PRODUTO = 2
# Acima disso o teste falha: sob carga, erro de servidor é problema, não ruído.
LIMITE_ERRO_PCT = 1.0

linhas_saida = []
falhas = []


def escrever(texto=""):
    print(texto)
    linhas_saida.append(texto)


def secao(titulo):
    escrever(f"\n=== {titulo} ===")


def checar(condicao, descricao):
    escrever(f"  [{'OK' if condicao else 'FALHOU'}] {descricao}")
    if not condicao:
        falhas.append(descricao)


def run_sql(sql, tuples_only=False):
    cmd = ["docker", "compose", "exec", "-T", POSTGRES_SERVICE, "psql", "-U", DB_USER, "-d", DB_NAME]
    if tuples_only:
        cmd += ["-t", "-A"]
    cmd += ["-c", sql]
    resultado = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=RAIZ)
    if resultado.returncode != 0:
        raise RuntimeError(f"Erro ao rodar SQL ({sql!r}): {resultado.stderr}")
    return resultado.stdout.strip()


def preparar_estoque(modo, quantos_produtos, necessario):
    """
    Carga não pode virar teste de 409: sem estoque, a api rejeita na hora e o
    teste mediria a rejeição, não a compra. Antes de começar, o estoque dos
    produtos usados é elevado para cobrir toda a carga com folga.
    """
    folga = int(necessario * 1.5) + 10
    if modo == "disputa":
        run_sql(
            f"INSERT INTO produtos (id, nome, estoque) "
            f"VALUES ({PRODUTO_DISPUTA}, 'Vaso de ceramica (teste)', {folga}) "
            f"ON CONFLICT (id) DO UPDATE SET estoque = {folga};"
        )
        return [PRODUTO_DISPUTA]

    ultimo = PRIMEIRO_PRODUTO + quantos_produtos - 1
    run_sql(f"UPDATE produtos SET estoque = {folga} WHERE id BETWEEN {PRIMEIRO_PRODUTO} AND {ultimo};")
    existentes = run_sql(
        f"SELECT id FROM produtos WHERE id BETWEEN {PRIMEIRO_PRODUTO} AND {ultimo} ORDER BY id;",
        tuples_only=True,
    )
    ids = [int(linha) for linha in existentes.splitlines() if linha.strip()]
    if not ids:
        raise RuntimeError(
            "Nenhum produto encontrado para a carga. Rodou o seed? "
            "(docker compose exec api python -m app.seed)"
        )
    return ids


def contar_pedidos():
    por_status = run_sql(
        "SELECT status, COUNT(*) FROM pedidos GROUP BY status ORDER BY status;", tuples_only=True
    )
    contagem = {}
    for linha in por_status.splitlines():
        if "|" in linha:
            status, quantidade = linha.split("|")
            contagem[status.strip()] = int(quantidade)
    return contagem


# --------------------------------------------------------------- amostras do monitor


class EspiaoDoMonitor:
    """
    Acompanha o painel durante a carga, numa thread à parte.

    Lê `/api/nivel1` e `/api/nivel2` (somente leitura). **Não** usa
    `/api/alertas`, que consome a fila de alertas: se usasse, os alertas
    sumiriam da faixa do painel justamente durante a demonstração ao vivo.
    """

    def __init__(self, intervalo=2.0):
        self._intervalo = intervalo
        self._parar = threading.Event()
        self._thread = None
        self.disponivel = False
        self.cpu = []
        self.containers_pico = {}
        self.inicio = None

    def _ciclo(self):
        with httpx.Client(timeout=5.0) as cliente:
            while not self._parar.is_set():
                try:
                    nivel1 = cliente.get(f"{MONITOR}/api/nivel1?janela=5").json()
                    amostra = nivel1.get("amostra") or {}
                    if amostra.get("cpu_total_pct") is not None:
                        self.cpu.append(amostra["cpu_total_pct"])

                    nivel2 = cliente.get(f"{MONITOR}/api/nivel2").json()
                    for container in nivel2.get("containers") or []:
                        nome = container["nome"]
                        memoria = container.get("memoria") or {}
                        pico = self.containers_pico.setdefault(nome, {"cpu": 0.0, "memoria_pct": 0.0})
                        pico["cpu"] = max(pico["cpu"], container.get("cpu_pct") or 0.0)
                        pico["memoria_pct"] = max(pico["memoria_pct"], memoria.get("uso_pct") or 0.0)
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
                self._parar.wait(self._intervalo)

    def iniciar(self):
        try:
            with httpx.Client(timeout=5.0) as cliente:
                cliente.get(f"{MONITOR}/saude").raise_for_status()
            self.disponivel = True
        except httpx.HTTPError:
            return False
        self.inicio = time.time()
        self._thread = threading.Thread(target=self._ciclo, name="espiao-monitor", daemon=True)
        self._thread.start()
        return True

    def parar(self):
        self._parar.set()
        if self._thread:
            self._thread.join(timeout=5)

    def eventos_do_periodo(self):
        if not self.disponivel or not self.inicio:
            return []
        try:
            with httpx.Client(timeout=5.0) as cliente:
                dados = cliente.get(f"{MONITOR}/api/eventos?minutos=60&limite=100").json()
            return [e for e in dados.get("eventos", []) if e["timestamp"] >= self.inicio]
        except (httpx.HTTPError, ValueError):
            return []


# ------------------------------------------------------------------------ a carga


async def _uma_requisicao(cliente, produto_id, atraso, resultados):
    """Espera até a hora marcada e dispara. O atraso é medido do início do teste."""
    if atraso > 0:
        await asyncio.sleep(atraso)
    partida = time.perf_counter()
    try:
        resposta = await cliente.post(f"{API}/pedidos", json={"produto_id": produto_id})
        codigo = resposta.status_code
    except httpx.HTTPError as erro:
        codigo = type(erro).__name__
    resultados.append((atraso, (time.perf_counter() - partida) * 1000, codigo))


async def _gerar_carga(produtos, taxa, duracao):
    total = int(taxa * duracao)
    resultados = []
    limites = httpx.Limits(max_connections=1000, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=30.0, limits=limites) as cliente:
        tarefas = [
            asyncio.create_task(
                _uma_requisicao(cliente, produtos[i % len(produtos)], i / taxa, resultados)
            )
            for i in range(total)
        ]
        await asyncio.gather(*tarefas)
    return resultados


def percentis(valores, quais=(50, 90, 95, 99)):
    """
    Percentis de latência, com o quick sort próprio (`estruturas/ordenacao.py`).
    Ordenar e indexar é O(n log n); como o relatório pede vários percentis de uma
    vez, ordenar uma vez sai mais barato que selecionar cada um separadamente.
    """
    if not valores:
        return {p: None for p in quais}
    ordenados = quick_sort(valores)
    saida = {}
    for p in quais:
        # Método do posto mais próximo: o p-ésimo percentil é o elemento na
        # posição ceil(p/100 · n), contando de 1.
        posicao = math.ceil((p / 100) * len(ordenados))
        indice = min(len(ordenados), max(1, posicao)) - 1
        saida[p] = round(ordenados[indice], 1)
    return saida


def por_segundo(resultados, duracao):
    """Agrupa as respostas por segundo de teste, para mostrar a carga ao longo do tempo."""
    baldes = [{"ok": 0, "sem_estoque": 0, "erro": 0, "latencias": []} for _ in range(int(duracao) + 1)]
    for atraso, latencia_ms, codigo in resultados:
        balde = baldes[min(len(baldes) - 1, int(atraso))]
        balde["latencias"].append(latencia_ms)
        if codigo == 201:
            balde["ok"] += 1
        elif codigo == 409:
            balde["sem_estoque"] += 1
        else:
            balde["erro"] += 1
    return baldes


# ---------------------------------------------------------------------- relatório


def relatar_carga(resultados, taxa, duracao, modo):
    secao("2. RESULTADO DA CARGA")
    total = len(resultados)
    ok = sum(1 for _, _, c in resultados if c == 201)
    sem_estoque = sum(1 for _, _, c in resultados if c == 409)
    erros = [(a, c) for a, _, c in resultados if c not in (201, 409)]
    latencias = [lat for _, lat, c in resultados if c in (201, 409)]
    p = percentis(latencias)

    escrever(f"  Taxa alvo: {taxa} req/s por {duracao}s (modo {modo}) = {total} requisições")
    escrever(f"  Respostas: {ok} criados (201) | {sem_estoque} sem estoque (409) | {len(erros)} erros")
    escrever(
        f"  Latência (ms): mediana {p[50]} | p90 {p[90]} | p95 {p[95]} | p99 {p[99]} | "
        f"máx {round(max(latencias), 1) if latencias else '—'}"
    )
    if erros:
        amostra_erros = {}
        for _, codigo in erros:
            amostra_erros[codigo] = amostra_erros.get(codigo, 0) + 1
        escrever(f"  Erros por tipo: {amostra_erros}")

    escrever("\n  Por segundo (cada # = 1 pedido criado):")
    for segundo, balde in enumerate(por_segundo(resultados, duracao)):
        if not balde["latencias"]:
            continue
        media = sum(balde["latencias"]) / len(balde["latencias"])
        barra = "#" * min(60, balde["ok"])
        marca = f" !{balde['erro']} erro(s)" if balde["erro"] else ""
        escrever(f"    {segundo:>3}s {balde['ok']:>4} ok {media:>7.0f} ms médio {barra}{marca}")

    erro_pct = 100 * len(erros) / total if total else 0
    checar(erro_pct <= LIMITE_ERRO_PCT, f"erros de servidor em {erro_pct:.2f}% das requisições (limite {LIMITE_ERRO_PCT}%)")
    checar(ok > 0, "pedidos criados durante a carga")
    return ok


def relatar_fila(antes, criados, janela):
    secao("3. FILA DEPOIS DA CARGA")
    escrever(
        "  Cada job simula 3 s de trabalho e são 3 réplicas do worker, então a fila\n"
        "  processa ~1 pedido/s enquanto a api aceita dezenas. A compra não espera o\n"
        "  pós-venda (Parte 1): o acúmulo abaixo é o desacoplamento funcionando."
    )
    inicio = contar_pedidos()
    pendentes_inicio = inicio.get("pendente", 0)
    escrever(f"\n  Logo após a carga: {pendentes_inicio} pendente(s), {inicio.get('processado', 0)} processado(s)")

    escrever(f"  Acompanhando por {janela}s:")
    fim = inicio
    marco = time.time()
    while time.time() - marco < janela:
        time.sleep(min(10, janela / 3))
        fim = contar_pedidos()
        escrever(
            f"    +{time.time() - marco:>4.0f}s  pendentes {fim.get('pendente', 0):>5} | "
            f"processados {fim.get('processado', 0):>6} | falhos {fim.get('falhou', 0)}"
        )

    drenados = inicio.get("processado", 0) - antes.get("processado", 0)
    decorrido = time.time() - marco
    processados_na_janela = fim.get("processado", 0) - inicio.get("processado", 0)
    vazao = processados_na_janela / decorrido if decorrido else 0
    pendentes_fim = fim.get("pendente", 0)
    escrever(f"\n  Vazão dos workers: {vazao:.2f} pedido(s)/s")
    if vazao > 0 and pendentes_fim:
        escrever(f"  Pendentes atuais: {pendentes_fim} (drenam em ~{pendentes_fim / vazao / 60:.1f} min nesse ritmo)")
    escrever(f"  Pedidos criados na carga: {criados} | já processados durante ela: {drenados}")

    checar(vazao > 0 or pendentes_fim == 0, "a fila está drenando (workers consumindo)")
    checar(fim.get("falhou", 0) == antes.get("falhou", 0), "nenhum pedido novo marcado como 'falhou'")


def relatar_monitor(espiao):
    secao("4. O QUE O MONITOR VIU")
    if not espiao.disponivel:
        escrever(f"  Painel não respondeu em {MONITOR}; a carga rodou sem essa parte.")
        escrever("  Suba o monitor (docker compose up -d monitor) para ter esta seção.")
        return

    if espiao.cpu:
        escrever(f"  CPU da máquina durante a carga: máx {max(espiao.cpu)}% | média {sum(espiao.cpu) / len(espiao.cpu):.1f}%")
    if espiao.containers_pico:
        escrever("  Pico por container:")
        ordenados = quick_sort(
            [(nome, pico) for nome, pico in espiao.containers_pico.items()],
            chave=lambda item: item[1]["cpu"],
            decrescente=True,
        )
        for nome, pico in ordenados[:8]:
            escrever(f"    {nome[:32]:<32} cpu máx {pico['cpu']:>6.1f}% | memória máx {pico['memoria_pct']:>5.1f}% do limite")

    eventos = espiao.eventos_do_periodo()
    escrever(f"  Eventos registrados durante a carga: {len(eventos)}")
    for evento in eventos[:10]:
        hora = datetime.fromtimestamp(evento["timestamp"]).strftime("%H:%M:%S")
        escrever(f"    {hora} [{evento['severidade']}] {evento['mensagem']}")
    if not eventos:
        escrever("    (nenhum: a carga não chegou a disparar alerta de memória nem derrubou serviço)")


def main():
    argumentos = argparse.ArgumentParser(description="Teste de carga do Origem (PI3).")
    argumentos.add_argument("--taxa", type=float, default=20, help="requisições por segundo (padrão 20)")
    argumentos.add_argument("--duracao", type=float, default=30, help="duração da carga em segundos (padrão 30)")
    argumentos.add_argument(
        "--modo",
        choices=("espalhado", "disputa"),
        default="espalhado",
        help="espalhado: vários produtos (vazão real). disputa: todos no mesmo produto (custo do lock)",
    )
    argumentos.add_argument("--produtos", type=int, default=200, help="quantos produtos usar no modo espalhado")
    argumentos.add_argument("--janela-fila", type=float, default=30, help="segundos acompanhando a fila drenar")
    opcoes = argumentos.parse_args()

    escrever("TESTE DE CARGA — ORIGEM (PI3, etapa 6)")
    escrever(f"Gerado em: {datetime.now().astimezone():%d/%m/%Y %H:%M:%S (UTC%z)}")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=RAIZ)
    escrever(f"Commit: {commit.stdout.strip()}")

    secao("1. PREPARAÇÃO")
    try:
        with httpx.Client(timeout=5.0) as cliente:
            cliente.get(f"{API}/").raise_for_status()
    except httpx.HTTPError as erro:
        escrever(f"  A api não respondeu em {API} ({type(erro).__name__}). Os containers estão de pé?")
        return 1

    produtos = preparar_estoque(opcoes.modo, opcoes.produtos, opcoes.taxa * opcoes.duracao)
    escrever(f"  Modo: {opcoes.modo} ({len(produtos)} produto(s), estoque elevado para cobrir a carga)")
    antes = contar_pedidos()
    escrever(f"  Pedidos no banco antes: {antes}")

    espiao = EspiaoDoMonitor()
    escrever(f"  Painel: {'acompanhando em ' + MONITOR if espiao.iniciar() else 'não disponível'}")

    escrever(f"\n  Disparando {int(opcoes.taxa * opcoes.duracao)} requisições a {opcoes.taxa}/s...")
    relogio = time.perf_counter()
    resultados = asyncio.run(_gerar_carga(produtos, opcoes.taxa, opcoes.duracao))
    decorrido = time.perf_counter() - relogio
    espiao.parar()
    escrever(f"  Carga terminou em {decorrido:.1f}s (vazão de entrada: {len(resultados) / decorrido:.1f} req/s)")

    criados = relatar_carga(resultados, opcoes.taxa, opcoes.duracao, opcoes.modo)
    relatar_fila(antes, criados, opcoes.janela_fila)
    relatar_monitor(espiao)

    secao("RESULTADO")
    escrever("Todas as verificações OK" if not falhas else f"{len(falhas)} verificação(ões) falharam:")
    for falha in falhas:
        escrever(f"  - {falha}")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas_saida) + "\n", encoding="utf-8")
    print(f"\nSalvo em {SAIDA.relative_to(RAIZ)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
