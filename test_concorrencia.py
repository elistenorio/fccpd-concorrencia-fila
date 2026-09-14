import httpx
import concurrent.futures

URL = "http://localhost:8000/pedidos"
PRODUTO_ID = 1
N_REQUISICOES = 10


def comprar(i):
    try:
        resp = httpx.post(URL, json={"produto_id": PRODUTO_ID}, timeout=10)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return i, resp.status_code, body
    except Exception as e:
        return i, "ERRO", str(e)


with concurrent.futures.ThreadPoolExecutor(max_workers=N_REQUISICOES) as executor:
    resultados = list(executor.map(comprar, range(N_REQUISICOES)))

sucessos = 0
conflitos = 0
outros = 0

for i, status, body in resultados:
    print(f"Requisição {i}: status={status} -> {body}")
    if status == 200:
        sucessos += 1
    elif status == 409:
        conflitos += 1
    else:
        outros += 1

print(f"\nTotal: {sucessos} sucesso(s), {conflitos} conflito(s), {outros} outro(s)")