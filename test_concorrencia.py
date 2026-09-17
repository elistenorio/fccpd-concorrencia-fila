"""
Teste de confiabilidade sob concorrência (Critérios 1, 2 e 5 da rubrica FCCPD).

O que este script prova, e como:
1. Cenário controlado -> reseta o produto com estoque conhecido antes do teste.
2. Ataque de concorrência -> dispara mais requisições do que o estoque disponível.
3. Veredito -> confere a resposta HTTP de cada requisição E o estado real do
   PostgreSQL depois do teste, e FALHA (exit code != 0) se algo não bater.

NOTA: a verificação no banco é feita via "docker compose exec ... psql", e não
via psycopg2/SQLAlchemy direto do Windows. Isso contorna um bug conhecido do
psycopg2 em Windows com locale pt-BR, que quebra com UnicodeDecodeError ao
tentar decodificar mensagens do sistema operacional durante a conexão.
"""

import asyncio
import subprocess

import httpx

# --- Configuração ---
API_URL = "http://localhost:8000/pedidos"
POSTGRES_SERVICE = "postgres"  # nome do serviço no docker-compose.yml
DB_USER = "fccpd"
DB_NAME = "fccpd_db"

PRODUTO_ID = 1
ESTOQUE_INICIAL = 10
N_REQUISICOES = 50  # mais requisições que o estoque -> simula "horário de pico"


def run_sql(sql: str, tuples_only: bool = False) -> str:
    """Executa um comando SQL dentro do container do Postgres via docker compose exec."""
    cmd = ["docker", "compose", "exec", "-T", POSTGRES_SERVICE, "psql", "-U", DB_USER, "-d", DB_NAME]
    if tuples_only:
        cmd += ["-t", "-A"]  # saída só com o valor, sem cabeçalho/formatação
    cmd += ["-c", sql]

    resultado = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if resultado.returncode != 0:
        raise RuntimeError(f"Erro ao rodar SQL ({sql!r}): {resultado.stderr}")
    return resultado.stdout.strip()


def preparar_produto():
    """Garante um cenário controlado e repetível: reseta o produto com estoque conhecido."""
    run_sql(f"DELETE FROM pedidos WHERE produto_id = {PRODUTO_ID};")
    run_sql(
        f"INSERT INTO produtos (id, nome, estoque) "
        f"VALUES ({PRODUTO_ID}, 'Vaso de ceramica (teste)', {ESTOQUE_INICIAL}) "
        f"ON CONFLICT (id) DO UPDATE SET estoque = {ESTOQUE_INICIAL};"
    )


def verificar_banco():
    """Consulta o estado real do banco após o teste (prova de integridade)."""
    estoque_final = int(run_sql(f"SELECT estoque FROM produtos WHERE id = {PRODUTO_ID};", tuples_only=True))
    pedidos_criados = int(
        run_sql(f"SELECT COUNT(*) FROM pedidos WHERE produto_id = {PRODUTO_ID};", tuples_only=True)
    )
    return estoque_final, pedidos_criados


async def comprar(client: httpx.AsyncClient, i: int):
    try:
        resp = await client.post(API_URL, json={"produto_id": PRODUTO_ID}, timeout=10)
        return i, resp.status_code
    except Exception as e:
        return i, f"ERRO: {e}"


async def rodar_ataque():
    async with httpx.AsyncClient() as client:
        tarefas = [comprar(client, i) for i in range(N_REQUISICOES)]
        return await asyncio.gather(*tarefas)


def main():
    preparar_produto()

    resultados = asyncio.run(rodar_ataque())

    sucessos = sum(1 for _, s in resultados if s == 201)
    conflitos = sum(1 for _, s in resultados if s == 409)
    outros = [(i, s) for i, s in resultados if s not in (201, 409)]

    estoque_final, pedidos_criados = verificar_banco()

    esperado_sucessos = ESTOQUE_INICIAL
    esperado_conflitos = N_REQUISICOES - ESTOQUE_INICIAL

    print("=== RELATÓRIO DE TESTE DE CONCORRÊNCIA ===")
    print(f"Estoque Inicial: {ESTOQUE_INICIAL}")
    print(f"Requisições Simultâneas: {N_REQUISICOES}\n")
    print("--- RESULTADOS HTTP ---")
    print(f"Sucessos (201 Created): {sucessos}")
    print(f"Conflitos (409 Conflict): {conflitos}")
    print(f"Erros Inesperados: {len(outros)}")
    for i, s in outros:
        print(f"  Requisição {i}: {s}")
    print("\n--- VERIFICAÇÃO NO BANCO DE DADOS ---")
    print(f"Estoque Final no PostgreSQL: {estoque_final}")
    print(f"Pedidos Criados no PostgreSQL: {pedidos_criados}")

    erros = []
    if sucessos != esperado_sucessos:
        erros.append(f"Esperado {esperado_sucessos} sucessos, obteve {sucessos}")
    if conflitos != esperado_conflitos:
        erros.append(f"Esperado {esperado_conflitos} conflitos, obteve {conflitos}")
    if outros:
        erros.append(f"{len(outros)} requisições com status inesperado")
    if estoque_final != 0:
        erros.append(f"Estoque final deveria ser 0, está {estoque_final}")
    if pedidos_criados != ESTOQUE_INICIAL:
        erros.append(f"Deveriam existir {ESTOQUE_INICIAL} pedidos, existem {pedidos_criados}")

    print()
    if erros:
        print("❌ FALHA: o sistema não manteve a consistência sob concorrência.")
        for e in erros:
            print(f"  - {e}")
        raise SystemExit(1)

    print("✅ VEREDITO: O sistema manteve a consistência sob alta concorrência. Zero erros.")


if __name__ == "__main__":
    main()