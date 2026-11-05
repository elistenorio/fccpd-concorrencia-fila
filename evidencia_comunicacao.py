"""
Evidência de que a comunicação entre os serviços funciona (FCCPD U2, critério 1).

Roda no host, com os containers de pé e o seed aplicado:
    docker compose up -d --build
    docker compose exec api python -m app.seed
    python evidencia_comunicacao.py

Gera evidencias/u2/comunicacao.txt com, para cada cenário, as respostas HTTP e
as linhas dos LOGS DOS PRÓPRIOS CONTAINERS que provam a troca de mensagens:

1. Síncrona (REST): api -> recomendacoes. O mesmo X-Request-ID aparece no log
   da api (quem chamou) e no do recomendacoes (quem respondeu).
2. Assíncrona (fila): api -> Redis/RQ -> worker. O job pedido-<id> aparece no
   log de um dos workers e o pedido muda para "processado".
3. Falha: recomendacoes fora do ar. A página do produto continua respondendo
   200, sem recomendações, em ~1 s na 1ª chamada e na hora nas seguintes
   (disjuntor); quando o serviço volta, as recomendações voltam.
4. Recálculo em lote no serviço de recomendações (o trecho paralelo).

Sai com código != 0 se algum cenário não se comportar como esperado.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent
SAIDA = RAIZ / "evidencias" / "u2" / "comunicacao.txt"
# 127.0.0.1 e não "localhost": no Windows, "localhost" tenta IPv6 primeiro e
# cada requisição ganha ~0,5 s de espera que não é da api.
API = "http://127.0.0.1:8000"
RECOMENDACOES = "http://127.0.0.1:8001"
# Um cliente só, reaproveitado: criar um por requisição (httpx.get avulso)
# custa ~0,3 s no Windows e entraria nos tempos medidos como se fosse da api.
HTTP = httpx.Client()
PRODUTOS = [2, 3, 4]
PAUSA_DISJUNTOR = 15

linhas_saida = []
falhas = []


def escrever(texto=""):
    print(texto)
    linhas_saida.append(texto)


def checar(condicao, descricao):
    escrever(f"  [{'OK' if condicao else 'FALHOU'}] {descricao}")
    if not condicao:
        falhas.append(descricao)


def compose(*args):
    r = subprocess.run(
        ["docker", "compose", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=RAIZ,
    )
    return (r.stdout + r.stderr).strip()


def logs(desde, servicos, termos):
    """Linhas dos logs dos serviços, desde o instante dado, que contêm algum dos termos."""
    texto = compose("logs", "--since", desde, *servicos)
    return [linha for linha in texto.splitlines() if any(t in linha for t in termos)]


def esperar(url, segundos=60):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        try:
            if HTTP.get(url, timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def agora_iso():
    return datetime.now(timezone.utc).isoformat()


def secao(titulo):
    escrever()
    escrever("=" * 78)
    escrever(titulo)
    escrever("=" * 78)


def cenario_sincrono():
    secao("1) Comunicação SÍNCRONA (HTTP/REST): api -> recomendacoes")
    escrever("A api recebe GET /produtos/{id} e chama GET /recomendacoes/{id} no serviço de")
    escrever("recomendações, repassando um X-Request-ID que aparece no log dos dois serviços.\n")
    desde = agora_iso()
    ids = []
    for produto_id in PRODUTOS:
        r = HTTP.get(f"{API}/produtos/{produto_id}", params={"limite": 3}, timeout=10)
        corpo = r.json()
        rid = r.headers.get("x-request-id")
        ids.append(rid)
        escrever(f"GET {API}/produtos/{produto_id}?limite=3 -> {r.status_code}  (X-Request-ID: {rid})")
        for rec in corpo.get("recomendacoes", []):
            escrever(f"    recomenda {rec['produto_id']:>5}  {rec['nome']:<28} score {rec['score']}")
        checar(r.status_code == 200 and corpo["recomendacoes_disponiveis"] and corpo["recomendacoes"],
               f"produto {produto_id} veio com recomendações do outro serviço")
    time.sleep(1)
    escrever("\nLogs dos containers (filtrados pelos X-Request-ID acima):")
    encontrados = logs(desde, ["api", "recomendacoes"], [f"[{rid}]" for rid in ids])
    for linha in encontrados:
        escrever("  " + linha)
    for rid in ids:
        checar(any(f"[{rid}]" in l and l.startswith("api") for l in encontrados)
               and any(f"[{rid}]" in l and l.startswith("recomendacoes") for l in encontrados),
               f"X-Request-ID {rid} aparece no log da api E no do recomendacoes")


def cenario_assincrono():
    secao("2) Comunicação ASSÍNCRONA (mensageria): api -> Redis/RQ -> worker (Parte 1)")
    escrever("A api grava o pedido como 'pendente', responde 201 e publica o job na fila;")
    escrever("um dos workers consome o job e marca o pedido como 'processado'.\n")
    desde = agora_iso()
    r = HTTP.post(f"{API}/pedidos", json={"produto_id": PRODUTOS[0], "cliente_id": 1}, timeout=10)
    escrever(f"POST {API}/pedidos -> {r.status_code}  {r.json()}")
    pedido_id = r.json().get("pedido_id")
    status = None
    for _ in range(30):
        status = HTTP.get(f"{API}/pedidos/{pedido_id}", timeout=5).json()["status"]
        if status != "pendente":
            break
        time.sleep(1)
    escrever(f"GET {API}/pedidos/{pedido_id} -> status '{status}'")
    checar(status == "processado", f"pedido {pedido_id} processado pelo worker")
    escrever("\nLogs dos containers (job pedido-%s):" % pedido_id)
    for linha in logs(desde, ["worker"], [f"pedido-{pedido_id}"]):
        escrever("  " + linha)


def cenario_falha():
    secao("3) FALHA: serviço de recomendações fora do ar")
    escrever("Recomendação é um extra da página do produto: com o serviço fora, a api deve")
    escrever("continuar respondendo 200, sem recomendações, sem travar.\n")
    desde = agora_iso()
    escrever("$ docker compose stop recomendacoes")
    compose("stop", "recomendacoes")
    try:
        for tentativa in (1, 2, 3):
            inicio = time.perf_counter()
            r = HTTP.get(f"{API}/produtos/{PRODUTOS[0]}", timeout=15)
            ms = (time.perf_counter() - inicio) * 1000
            escrever(f"GET /produtos/{PRODUTOS[0]} (tentativa {tentativa}) -> {r.status_code} em {ms:.0f} ms, "
                     f"recomendacoes_disponiveis={r.json()['recomendacoes_disponiveis']}")
            checar(r.status_code == 200 and not r.json()["recomendacoes_disponiveis"] and ms < 2000,
                   "página respondeu 200 sem recomendações em menos de 2 s")
    finally:
        escrever("\n$ docker compose start recomendacoes")
        compose("start", "recomendacoes")
    esperar(f"{RECOMENDACOES}/")
    escrever(f"(aguardando {PAUSA_DISJUNTOR} s para o disjuntor da api fechar)")
    time.sleep(PAUSA_DISJUNTOR + 1)
    r = HTTP.get(f"{API}/produtos/{PRODUTOS[0]}", timeout=10)
    escrever(f"GET /produtos/{PRODUTOS[0]} depois que o serviço voltou -> {r.status_code}, "
             f"recomendacoes_disponiveis={r.json()['recomendacoes_disponiveis']}")
    checar(r.json()["recomendacoes_disponiveis"], "recomendações voltaram sem reiniciar a api")
    escrever("\nLogs da api no período:")
    for linha in logs(desde, ["api"], ["Recomendações em pausa", "indisponível", "GET http://recomendacoes"]):
        escrever("  " + linha)


def cenario_recalculo():
    secao("4) Recálculo em lote no serviço de recomendações (trecho paralelo)")
    desde = agora_iso()
    r = HTTP.post(f"{RECOMENDACOES}/recomendacoes/recalcular", params={"modo": "processos"}, timeout=300)
    escrever(f"POST {RECOMENDACOES}/recomendacoes/recalcular?modo=processos -> {r.status_code}")
    for chave, valor in r.json().items():
        escrever(f"    {chave}: {valor}")
    checar(r.status_code == 200, "recálculo concluído")
    escrever("\nLog do recomendacoes:")
    for linha in logs(desde, ["recomendacoes"], ["Recálculo"]):
        escrever("  " + linha)


def main():
    if not (esperar(f"{API}/", 5) and esperar(f"{RECOMENDACOES}/", 5)):
        print("Suba os containers antes: docker compose up -d --build", file=sys.stderr)
        return 1

    escrever("Evidência de comunicação entre serviços — FCCPD U2")
    escrever(f"Gerado em: {datetime.now().astimezone():%d/%m/%Y %H:%M:%S (UTC%z)}")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=RAIZ)
    escrever(f"Commit: {commit.stdout.strip()}")
    escrever("\nContainers:")
    for linha in compose("ps", "--format", "{{.Service}}\t{{.Status}}\t{{.Ports}}").splitlines():
        escrever("  " + linha)

    cenario_sincrono()
    cenario_assincrono()
    cenario_falha()
    cenario_recalculo()

    secao("RESULTADO")
    escrever("Todos os cenários OK" if not falhas else f"{len(falhas)} verificação(ões) falharam:")
    for f in falhas:
        escrever(f"  - {f}")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas_saida) + "\n", encoding="utf-8")
    print(f"\nSalvo em {SAIDA.relative_to(RAIZ)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
