"""
Roteiro de demonstração do modelo de domínio (para a apresentação):

    python -m dominio.demo

Cadastra produtos de artesãos, dá entrada em lotes com validade, registra
pedidos com prioridade, atende (FEFO), mostra as buscas e desfaz passo a passo.
"""

from datetime import date, timedelta

from dominio import Loja

HOJE = date(2026, 11, 26)


def reais(centavos):
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def mostrar_estoque(loja):
    for p in loja.estoque:
        lotes = ", ".join((f"{l.quantidade} vence {l.validade}" if l.validade else f"{l.quantidade} sem validade") for l in p.lotes) or "sem lotes"
        print(f"   {p.codigo:7} {p.nome:22} {reais(p.preco_centavos):>11}  [{lotes}]")


def main():
    loja = Loja(prioridade=True)
    print("1. Cadastro (ListaEncadeada + TabelaHash por código + ArvoreBusca por nome)")
    loja.cadastrar("VAS-01", "Vaso de barro", "cerâmica", "Dona Ana (Tracunhaém)", 8900)
    loja.cadastrar("VAS-02", "Vaso pintado", "cerâmica", "Dona Ana (Tracunhaém)", 12000)
    loja.cadastrar("REN-01", "Renda renascença", "renda", "Dona Cida (Poção)", 25000)
    loja.cadastrar("DOC-01", "Doce de caju", "alimento", "Seu Zé (Garanhuns)", 1500)
    loja.cadastrar("BOL-01", "Bolsa de palha", "trançado", "Dona Lia (Caruaru)", 6500)

    print("\n2. Entrada de lotes: inserção ordenada por validade (o doce que vence antes sai primeiro)")
    loja.entrada("VAS-01", 4)
    loja.entrada("REN-01", 2)
    loja.entrada("BOL-01", 3)
    loja.entrada("DOC-01", 10, HOJE + timedelta(days=60))
    loja.entrada("DOC-01", 6, HOJE + timedelta(days=7))
    loja.entrada("DOC-01", 3, HOJE - timedelta(days=2))
    mostrar_estoque(loja)

    print("\n3. Consultas")
    print("   hash   consultar('REN-01')       ->", loja.estoque.consultar("REN-01"))
    print("   árvore buscar_prefixo('vaso')    ->", [p.codigo for p in loja.estoque.buscar_prefixo("vaso")])
    print("   binária faixa R$ 60 a R$ 130      ->", [p.codigo for p in loja.estoque.faixa_de_preco(6000, 13000)])
    print("   vencidos em", HOJE, "            ->", [(p.codigo, l.quantidade) for p, l in loja.estoque.vencidos(HOJE)])

    print("\n4. Pedidos na fila com prioridade (empate = ordem de chegada)")
    loja.registrar_pedido("DOC-01", 8, prioridade=1, cliente="Ana")
    loja.registrar_pedido("REN-01", 3, prioridade=1, cliente="Bia")
    loja.registrar_pedido("VAS-01", 2, prioridade=3, cliente="Caio (atacado)")
    loja.registrar_pedido("BOL-01", 1, prioridade=1, cliente="Duda")
    print("   ordem de atendimento:", [f"#{p.id} {p.cliente}" for p in loja.pedidos])

    print("\n5. Atendimento (saída FEFO, pula lote vencido; tudo ou nada)")
    while True:
        pedido = loja.processar_proximo(HOJE)
        if pedido is None:
            break
        print(f"   #{pedido.id} {pedido.cliente:15} {pedido.status}" + (f" ({pedido.motivo})" if pedido.motivo else ""))
    mostrar_estoque(loja)

    print("\n6. Histórico (Pilha): últimas operações")
    for op in loja.ultimas_operacoes(5):
        print("   ", op.descricao)

    print("\n7. Desfazer as 4 últimas (atendimentos voltam, estoque volta)")
    for _ in range(4):
        print("    desfez:", loja.desfazer().descricao)
    mostrar_estoque(loja)
    print("   fila de novo:", [f"#{p.id} {p.cliente}" for p in loja.pedidos])


if __name__ == "__main__":
    main()
