"""
Demonstração do Passo 2 do PI3: produtor-consumidor + condição de corrida.

Produtores geram itens (valores 1..N) e colocam no BufferLimitado; consumidores
retiram e registram cada item nas estatísticas compartilhadas. Roda o mesmo
pipeline duas vezes:

1. com EstatisticasInseguras -> o total final fica MENOR que o esperado
   (atualizações perdidas pela condição de corrida);
2. com EstatisticasSeguras   -> o total bate exatamente.

O buffer é o mesmo nas duas rodadas e é sempre seguro: a corrida demonstrada é
no estado compartilhado dos consumidores, não na fila.

Uso:
    python -m concorrencia.demo_corrida
    python -m concorrencia.demo_corrida --produtores 4 --consumidores 4 --itens 5000

Sai com código 1 se a versão segura não bater (para usar no CI).
"""

import argparse
import sys
import threading
import time

from concorrencia.buffer import BufferLimitado
from concorrencia.estatisticas import EstatisticasInseguras, EstatisticasSeguras

# Sinal de fim: cada consumidor que retira um FIM encerra.
FIM = object()


def produtor(buffer, inicio, quantidade):
    for valor in range(inicio, inicio + quantidade):
        buffer.colocar(valor)


def consumidor(buffer, estatisticas):
    while True:
        item = buffer.retirar()
        if item is FIM:
            return
        estatisticas.registrar(item)


def executar(estatisticas, n_produtores, n_consumidores, itens_por_produtor, capacidade):
    buffer = BufferLimitado(capacidade)
    produtores = [
        threading.Thread(target=produtor, args=(buffer, 1 + i * itens_por_produtor, itens_por_produtor))
        for i in range(n_produtores)
    ]
    consumidores = [
        threading.Thread(target=consumidor, args=(buffer, estatisticas))
        for _ in range(n_consumidores)
    ]
    inicio = time.perf_counter()
    for t in consumidores + produtores:
        t.start()
    for t in produtores:
        t.join()
    # Só depois que todos os produtores terminaram: um FIM por consumidor.
    for _ in consumidores:
        buffer.colocar(FIM)
    for t in consumidores:
        t.join()
    return time.perf_counter() - inicio


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--produtores", type=int, default=3)
    parser.add_argument("--consumidores", type=int, default=3)
    parser.add_argument("--itens", type=int, default=2000, help="itens por produtor")
    parser.add_argument("--capacidade", type=int, default=16, help="capacidade do buffer")
    args = parser.parse_args()

    total_itens = args.produtores * args.itens
    soma_esperada = total_itens * (total_itens + 1) // 2

    print(
        f"{args.produtores} produtores x {args.itens} itens, {args.consumidores} consumidores, "
        f"buffer de {args.capacidade} posições"
    )
    print(f"Esperado: total={total_itens} soma={soma_esperada}\n")

    resultados = {}
    for nome, classe in (("SEM lock", EstatisticasInseguras), ("COM lock", EstatisticasSeguras)):
        estatisticas = classe()
        duracao = executar(estatisticas, args.produtores, args.consumidores, args.itens, args.capacidade)
        perdidas = total_itens - estatisticas.total
        correto = estatisticas.total == total_itens and estatisticas.soma == soma_esperada
        resultados[nome] = correto
        print(
            f"[{nome}] total={estatisticas.total} soma={estatisticas.soma} "
            f"perdidas={perdidas} ({perdidas / total_itens:.1%}) "
            f"tempo={duracao:.2f}s -> {'CORRETO' if correto else 'ERRADO (condição de corrida)'}"
        )

    return 0 if resultados["COM lock"] else 1


if __name__ == "__main__":
    sys.exit(main())
