import subprocess
import time
import httpx

API_URL = "http://localhost:8000"
PRODUTO_ID = 1
QTD_PEDIDOS = 5
TIMEOUT_SEGUNDOS = 15


def psql(comando: str) -> str:
    """Executa um comando SQL via docker compose exec (evita o bug de encoding no Windows)."""
    resultado = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "postgres",
            "psql", "-U", "fccpd", "-d", "fccpd_db", "-t", "-c", comando,
        ],
        capture_output=True, text=True,
    )
    return resultado.stdout.strip()


def resetar_produto():
    """Garante um produto com estoque conhecido pro teste."""
    psql(f"DELETE FROM pedidos WHERE produto_id = {PRODUTO_ID};")
    psql(f"DELETE FROM produtos WHERE id = {PRODUTO_ID};")
    psql(
        f"INSERT INTO produtos (id, nome, estoque) "
        f"VALUES ({PRODUTO_ID}, 'Produto Teste Fila', 100);"
    )


def contar_status(status: str) -> int:
    saida = psql(
        f"SELECT COUNT(*) FROM pedidos "
        f"WHERE produto_id = {PRODUTO_ID} AND status = '{status}';"
    )
    return int(saida)


def test_fila_processa_pedidos_assincronamente():
    resetar_produto()

    # Dispara os pedidos e guarda os ids retornados
    pedido_ids = []
    for _ in range(QTD_PEDIDOS):
        resposta = httpx.post(f"{API_URL}/pedidos", json={"produto_id": PRODUTO_ID})
        assert resposta.status_code == 201, f"Falha ao criar pedido: {resposta.text}"
        pedido_ids.append(resposta.json()["pedido_id"])

    # Logo após criar, esperado que ainda estejam pendentes (fila é assíncrona)
    pendentes_no_inicio = contar_status("pendente")
    assert pendentes_no_inicio > 0, (
        "Nenhum pedido ficou 'pendente' após a criação — "
        "o processamento parece estar acontecendo de forma síncrona, não pela fila."
    )

    # Faz polling até todos ficarem "processado" ou estourar o timeout
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