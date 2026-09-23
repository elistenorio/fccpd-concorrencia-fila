"""
Teste de resiliência da fila (Critério 4 da rubrica FCCPD).

O que este script prova, e como:
1. A loja continua vendendo com a fila fora -> derruba o Redis e faz compras:
   todas têm que responder 201, e rápido (a API publica na fila só depois de
   responder, então nenhuma compra espera pelo Redis).
2. Nenhum pedido se perde -> os pedidos ficam "pendente" no banco enquanto o
   Redis está fora; depois que ele volta, o reconciliador publica os jobs e
   todos têm que chegar a "processado".
3. O estoque continua correto -> baixa exatamente uma unidade por compra.

Roda no host (fora do container), como o test_concorrencia.py, porque precisa
do docker para derrubar e religar o Redis. O Redis é sempre religado no final,
mesmo se o teste falhar.
"""

import statistics
import subprocess
import time

import httpx

from test_concorrencia import ESTOQUE_INICIAL, PRODUTO_ID, preparar_produto, run_sql

API_URL = "http://localhost:8000/pedidos"
QTD_PEDIDOS = 5
LIMITE_LATENCIA_MS = 1000
# Margem para: workers reconectarem (backoff exponencial do RQ) + reconciliador
# (pedido pendente há 10 s, ciclo de 5 s) + 3 s de processamento por pedido.
TIMEOUT_PROCESSAMENTO = 120


def docker_compose(*args: str):
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True)


def contar_status(status: str) -> int:
    return int(run_sql(
        f"SELECT COUNT(*) FROM pedidos WHERE produto_id = {PRODUTO_ID} AND status = '{status}';",
        tuples_only=True,
    ))


def estoque_atual() -> int:
    return int(run_sql(f"SELECT estoque FROM produtos WHERE id = {PRODUTO_ID};", tuples_only=True))


def main():
    preparar_produto()

    docker_compose("stop", "redis")
    try:
        respostas, latencias_ms = [], []
        for _ in range(QTD_PEDIDOS):
            inicio = time.perf_counter()
            try:
                respostas.append(httpx.post(API_URL, json={"produto_id": PRODUTO_ID}, timeout=30).status_code)
            except httpx.HTTPError as e:
                respostas.append(f"ERRO: {e}")
            latencias_ms.append((time.perf_counter() - inicio) * 1000)

        pendentes_com_redis_fora = contar_status("pendente")
        estoque_com_redis_fora = estoque_atual()
    finally:
        docker_compose("start", "redis")

    inicio = time.time()
    while time.time() - inicio < TIMEOUT_PROCESSAMENTO:
        if contar_status("processado") == QTD_PEDIDOS:
            break
        time.sleep(1)
    tempo_recuperacao = time.time() - inicio
    processados = contar_status("processado")

    sucessos = sum(1 for s in respostas if s == 201)

    print("=== RELATÓRIO DE TESTE DE RESILIÊNCIA (REDIS FORA DO AR) ===")
    print(f"Compras feitas com o Redis desligado: {QTD_PEDIDOS}\n")
    print("--- COM O REDIS FORA ---")
    print(f"Respostas 201 Created: {sucessos} | Outras: {[s for s in respostas if s != 201]}")
    print(f"Latência do POST /pedidos: mediana {statistics.median(latencias_ms):.0f} ms | "
          f"máx {max(latencias_ms):.0f} ms")
    print(f"Pedidos 'pendente' no PostgreSQL: {pendentes_com_redis_fora}")
    print(f"Estoque no PostgreSQL: {ESTOQUE_INICIAL} -> {estoque_com_redis_fora}")
    print("\n--- DEPOIS DE RELIGAR O REDIS ---")
    print(f"Pedidos 'processado': {processados} de {QTD_PEDIDOS} em {tempo_recuperacao:.1f}s "
          f"(republicados pelo reconciliador)")

    erros = []
    if sucessos != QTD_PEDIDOS:
        erros.append(f"Esperado {QTD_PEDIDOS} compras com 201, obteve {sucessos}")
    if max(latencias_ms) > LIMITE_LATENCIA_MS:
        erros.append(f"Uma compra com a fila fora levou {max(latencias_ms):.0f} ms (limite {LIMITE_LATENCIA_MS} ms)")
    if pendentes_com_redis_fora != QTD_PEDIDOS:
        erros.append(f"Esperado {QTD_PEDIDOS} pedidos pendentes com o Redis fora, havia {pendentes_com_redis_fora}")
    if estoque_com_redis_fora != ESTOQUE_INICIAL - QTD_PEDIDOS:
        erros.append(f"Estoque deveria ser {ESTOQUE_INICIAL - QTD_PEDIDOS}, está {estoque_com_redis_fora}")
    if processados != QTD_PEDIDOS:
        erros.append(f"Só {processados} de {QTD_PEDIDOS} pedidos processados em {TIMEOUT_PROCESSAMENTO}s")

    print()
    if erros:
        print("❌ FALHA: a queda da fila afetou a loja ou perdeu pedidos.")
        for e in erros:
            print(f"  - {e}")
        raise SystemExit(1)

    print("✅ VEREDITO: Com o Redis fora, a loja continuou vendendo e nenhum pedido se perdeu.")


if __name__ == "__main__":
    main()
