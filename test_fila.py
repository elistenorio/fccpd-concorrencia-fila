import os
import time
import httpx
import psycopg2

API_URL = "http://localhost:8000"
PRODUTO_ID = 1
QTD_PEDIDOS = 5
TIMEOUT_SEGUNDOS = 30

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
    for _ in range(QTD_PEDIDOS):
        resposta = httpx.post(f"{API_URL}/pedidos", json={"produto_id": PRODUTO_ID})
        assert resposta.status_code == 201, f"Falha ao criar pedido: {resposta.text}"
        pedido_ids.append(resposta.json()["pedido_id"])

    pendentes_no_inicio = contar_status("pendente")
    assert pendentes_no_inicio > 0, (
        "Nenhum pedido ficou 'pendente' após a criação — "
        "o processamento parece estar acontecendo de forma síncrona, não pela fila."
    )

    inicio = time.time()
    while time.time() - inicio < TIMEOUT_SEGUNDOS:
        if contar_status("processado") == QTD_PEDIDOS:
            break
        time.sleep(1)

    processados = contar_status("processado")
    assert processados == QTD_PEDIDOS, (
        f"Esperado {QTD_PEDIDOS} pedidos processados após {TIMEOUT_SEGUNDOS}s, "
        f"mas só {processados} foram. O worker pode não estar consumindo a fila."
    )

    print(
        f"\n[OK] {QTD_PEDIDOS} pedidos criados, todos processados pelo worker "
        f"em até {TIMEOUT_SEGUNDOS}s."
    )


if __name__ == "__main__":
    test_fila_processa_pedidos_assincronamente()