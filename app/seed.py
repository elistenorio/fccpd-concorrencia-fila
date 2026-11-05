"""
Popula o banco com um histórico de compras grande e realista para as
recomendações e o benchmark da FCCPD (Parte 2).

Uso (com o compose de pé):
    docker compose exec api python -m app.seed
    docker compose exec api python -m app.seed --produtos 4000 --clientes 20000 --pedidos 160000

ATENÇÃO: apaga todos os produtos e pedidos, exceto o produto 1 e os pedidos
dele, que são usados pelos testes da Parte 1. É um banco de desenvolvimento.

Como os dados imitam um marketplace de verdade (senão as recomendações seriam
ruído aleatório e o benchmark não representaria o produto):
- cada artesão trabalha com uma categoria, e cada produto é de um artesão;
- cada cliente tem 1 ou 2 categorias preferidas e compra nelas 80% das vezes;
- popularidade desigual: poucos produtos e clientes concentram muitas compras
  (distribuição de Pareto), como em qualquer loja.
Com a mesma --semente, os dados gerados são sempre os mesmos, então o
benchmark é reproduzível.
"""

import argparse
import random
import time
from datetime import datetime, timedelta, timezone

CATEGORIAS = [
    "cerâmica", "barro", "madeira", "couro", "renda",
    "bordado", "palha", "xilogravura", "tecelagem", "bijuteria",
]
CHANCE_CATEGORIA_PREFERIDA = 0.8
JANELA_DIAS = 180


def _acumulados(pesos):
    total, acumulados = 0.0, []
    for p in pesos:
        total += p
        acumulados.append(total)
    return acumulados


def gerar_dados(n_produtos, n_clientes, n_pedidos, semente=42):
    """
    Devolve (produtos, pedidos). Produtos são dicts prontos para o INSERT;
    pedidos referenciam o produto pela POSIÇÃO na lista (o id só existe depois
    do INSERT).
    """
    rng = random.Random(semente)

    n_artesaos = max(1, n_produtos // 10)
    categoria_do_artesao = [rng.choice(CATEGORIAS) for _ in range(n_artesaos)]

    produtos = []
    indices_por_categoria = {c: [] for c in CATEGORIAS}
    for i in range(n_produtos):
        artesao = rng.randrange(n_artesaos)
        categoria = categoria_do_artesao[artesao]
        indices_por_categoria[categoria].append(i)
        produtos.append({
            "nome": f"Peça de {categoria} {i + 1}",
            "estoque": rng.randint(1, 50),
            "categoria": categoria,
            "artesao": f"Artesão {artesao + 1:04d}",
        })

    popularidade = [rng.paretovariate(1.5) for _ in range(n_produtos)]
    todos = list(range(n_produtos))
    acum_todos = _acumulados(popularidade)
    acum_por_categoria = {
        c: _acumulados([popularidade[i] for i in idx])
        for c, idx in indices_por_categoria.items() if idx
    }

    preferencias = [
        rng.sample(CATEGORIAS, rng.choice((1, 2))) for _ in range(n_clientes)
    ]
    atividade = [rng.paretovariate(2.0) for _ in range(n_clientes)]
    compradores = rng.choices(range(n_clientes), weights=atividade, k=n_pedidos)

    agora = datetime.now(timezone.utc)
    pedidos = []
    for cliente in compradores:
        categoria = rng.choice(preferencias[cliente])
        if rng.random() < CHANCE_CATEGORIA_PREFERIDA and categoria in acum_por_categoria:
            produto = rng.choices(indices_por_categoria[categoria], cum_weights=acum_por_categoria[categoria])[0]
        else:
            produto = rng.choices(todos, cum_weights=acum_todos)[0]
        pedidos.append({
            "produto": produto,
            "cliente_id": cliente + 1,
            "criado_em": agora - timedelta(seconds=rng.randrange(JANELA_DIAS * 86400)),
        })
    return produtos, pedidos


def gravar(produtos, pedidos):
    # Imports aqui dentro: gerar_dados() é testado sem banco e sem DATABASE_URL.
    from sqlalchemy import insert, text

    from app.database import engine
    from app.models import Pedido, Produto, criar_e_migrar

    criar_e_migrar(engine)
    with engine.begin() as con:
        con.execute(text("DELETE FROM pedidos WHERE produto_id <> 1"))
        con.execute(text("DELETE FROM produtos WHERE id <> 1"))
        # O produto 1 dos testes é inserido com id explícito, o que não avança
        # a sequência. Garante que o seed nunca receba o id 1.
        con.execute(text(
            "SELECT setval(pg_get_serial_sequence('produtos', 'id'), "
            "GREATEST((SELECT COALESCE(MAX(id), 0) FROM produtos), 1))"
        ))
        ids = con.execute(
            insert(Produto).returning(Produto.id, sort_by_parameter_order=True), produtos
        ).scalars().all()
        con.execute(insert(Pedido), [
            {
                "produto_id": ids[p["produto"]],
                "cliente_id": p["cliente_id"],
                "criado_em": p["criado_em"],
                # Histórico já processado: se ficasse "pendente", o
                # reconciliador publicaria um job de 3 s para cada um.
                "status": "processado",
            }
            for p in pedidos
        ])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--produtos", type=int, default=2000)
    parser.add_argument("--clientes", type=int, default=10000)
    parser.add_argument("--pedidos", type=int, default=80000)
    parser.add_argument("--semente", type=int, default=42)
    args = parser.parse_args()

    inicio = time.perf_counter()
    produtos, pedidos = gerar_dados(args.produtos, args.clientes, args.pedidos, args.semente)
    gerado = time.perf_counter()
    gravar(produtos, pedidos)
    fim = time.perf_counter()

    compradores = [set() for _ in produtos]
    for p in pedidos:
        compradores[p["produto"]].add(p["cliente_id"])
    media = sum(len(c) for c in compradores) / len(compradores)
    sem_venda = sum(1 for c in compradores if not c)

    print(f"Produtos: {len(produtos)} ({max(1, len(produtos) // 10)} artesãos, {len(CATEGORIAS)} categorias)")
    print(f"Clientes: {args.clientes}   Pedidos: {len(pedidos)}   Semente: {args.semente}")
    print(f"Compradores distintos por produto: média {media:.1f}; {sem_venda} produtos sem venda")
    print(f"Tempo: geração {gerado - inicio:.1f}s, gravação {fim - gerado:.1f}s")


if __name__ == "__main__":
    main()
