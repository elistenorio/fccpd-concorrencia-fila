import os
import statistics
import time
import httpx
import psycopg2

API_URL = "http://localhost:8000"
PRODUTO_ID = 1
QTD_PEDIDOS = 5
TIMEOUT_SEGUNDOS = 30
DURACAO_JOB_MS = 3000  # o worker simula 3 s de trabalho por pedido (app/tasks.py)
LIMITE_LATENCIA_MS = 500  # a compra tem que responder bem antes do job terminar

DATABASE_URL = os.getenv("DATABASE_URL")


def conectar():
    return psycopg2.connect(DATABASE_URL)


def resetar_produto():
    conn = conectar()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pedidos WHERE produto_id = %s;", (PRODUTO_ID,))
        cur.execute("DELETE FROM produtos WHERE id = %s;", (PRODUTO_ID,))
        cur.execute(
            "INSERT INTO produtos (id, nome, estoque) VALUES (%s, %s, %s);",
            (PRODUTO_ID, "Produto Teste Fila", 100),
        )
    conn.close()


def contar_status(status: str) -> int:
    conn = conectar()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM pedidos WHERE produto_id = %s AND status = %s;",
            (PRODUTO_ID, status),
        )
        total = cur.fetchone()[0]
    conn.close()
    return total


def test_fila_processa_pedidos_assincronamente():
    resetar_produto()

    pedido_ids = []
    latencias_ms = []
    for _ in range(QTD_PEDIDOS):
        inicio_req = time.perf_counter()
        resposta = httpx.post(f"{API_URL}/pedidos", json={"produto_id": PRODUTO_ID})
        latencias_ms.append((time.perf_counter() - inicio_req) * 1000)
        assert resposta.status_code == 201, f"Falha ao criar pedido: {resposta.text}"
        # A resposta já sai "pendente": a API não esperou o worker processar.
        assert resposta.json()["status"] == "pendente", (
            f"Pedido respondeu com status {resposta.json()['status']!r} — "
            "o processamento parece estar acontecendo de forma síncrona, não pela fila."
        )
        pedido_ids.append(resposta.json()["pedido_id"])

    assert max(latencias_ms) < LIMITE_LATENCIA_MS, (
        f"A compra mais lenta levou {max(latencias_ms):.0f} ms: a API está esperando o "
        "trabalho pesado em vez de deixá-lo para a fila."
    )

    inicio = time.time()
    while time.time() - inicio < TIMEOUT_SEGUNDOS:
        if contar_status("processado") == QTD_PEDIDOS:
            break
        time.sleep(1)
    tempo_total = time.time() - inicio

    processados = contar_status("processado")
    assert processados == QTD_PEDIDOS, (
        f"Esperado {QTD_PEDIDOS} pedidos processados após {TIMEOUT_SEGUNDOS}s, "
        f"mas só {processados} foram. O worker pode não estar consumindo a fila."
    )

    print("=== RELATÓRIO DE TESTE DA FILA ===")
    print(f"Pedidos criados: {QTD_PEDIDOS} (todos responderam 201 com status 'pendente')")
    print(
        f"Latência do POST /pedidos: mín {min(latencias_ms):.0f} ms | "
        f"mediana {statistics.median(latencias_ms):.0f} ms | máx {max(latencias_ms):.0f} ms "
        f"(cada job leva {DURACAO_JOB_MS} ms)"
    )
    print(f"Todos processados pelos workers em {tempo_total:.1f}s (limite: {TIMEOUT_SEGUNDOS}s)")
    print(
        f"\n[OK] {QTD_PEDIDOS} pedidos criados, todos processados pelo worker "
        f"em até {TIMEOUT_SEGUNDOS}s, sem a compra esperar pelo processamento."
    )


if __name__ == "__main__":
    test_fila_processa_pedidos_assincronamente()