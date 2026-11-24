"""
Benchmark entre os algoritmos próprios (PI3, bônus "benchmark entre algoritmos").

Compara os quatro algoritmos de ordenação e as duas buscas de `estruturas/`,
para medir o que a tabela de complexidade promete em vez de só afirmar.

    python -m estruturas.benchmark              # completo (~2 min)
    python -m estruturas.benchmark --rapido     # metade das repetições

Gera `evidencias/pi3/benchmark-algoritmos.md` (tabelas) e
`evidencias/pi3/benchmark-algoritmos.csv` (cada medição individual).

**Metodologia**, a mesma do benchmark da FCCPD (`recomendacoes/benchmark.py`):

- 1 execução de aquecimento descartada + N medições por configuração;
- reportamos a **mediana** (menos sensível a picos do sistema), com mínimo e máximo;
- a mesma entrada para todos os algoritmos na mesma configuração (semente fixa),
  então a comparação é justa;
- a saída de cada algoritmo é conferida contra o `sorted()` do Python em toda
  configuração: medir algoritmo errado não mede nada.

**Por que o `sorted()` aparece nas tabelas.** Não é concorrente: ele é C
compilado (Timsort) e os nossos são Python interpretado, então perder para ele
por uma ordem de grandeza é esperado e não diz nada sobre o algoritmo. Ele está
aqui como régua, para separar "este algoritmo é pior" de "Python é lento".

**Os cinco cenários** existem porque a complexidade média esconde o que importa:

| Cenário | O que expõe |
|---|---|
| `aleatorio` | o caso médio da tabela |
| `ordenado` | o melhor caso do insertion (O(n)) e o pior caso clássico do quick sort com pivô ingênuo |
| `invertido` | o pior caso do insertion |
| `quase_ordenado` | o caso real de dados que chegam quase em ordem (amostras do monitor, por exemplo) |
| `muitos_repetidos` | o comportamento da partição com chaves iguais |
"""

import argparse
import csv
import os
import platform
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from estruturas.busca import busca_binaria, busca_sequencial
from estruturas.ordenacao import insertion_sort, merge_sort, quick_sort, selection_sort

RAIZ = Path(__file__).resolve().parent.parent
SAIDA_MD = RAIZ / "evidencias" / "pi3" / "benchmark-algoritmos.md"
SAIDA_CSV = RAIZ / "evidencias" / "pi3" / "benchmark-algoritmos.csv"

SEMENTE = 42

# Os O(n²) ficam inviáveis antes dos O(n log n): acima deste tamanho só medimos
# merge e quick.
TETO_QUADRATICOS = 2000

TAMANHOS = (250, 500, 1000, 2000, 5000, 20000)
TAMANHOS_BUSCA = (1000, 10_000, 100_000)

ALGORITMOS = (
    ("insertion", insertion_sort, True),
    ("selection", selection_sort, True),
    ("merge", merge_sort, False),
    ("quick", quick_sort, False),
    ("sorted (C)", lambda itens: sorted(itens), False),
)

linhas_md = []
medicoes = []
# (cenario, n, algoritmo) -> tempo mediano, para a interpretação usar os números
# realmente medidos nesta máquina em vez de afirmações fixas.
resultados = {}


def escrever(texto=""):
    print(texto)
    linhas_md.append(texto)


# ---------------------------------------------------------------------- entradas


def gerar(cenario, n, aleatorio):
    if cenario == "aleatorio":
        return [aleatorio.randrange(n * 10) for _ in range(n)]
    if cenario == "ordenado":
        return list(range(n))
    if cenario == "invertido":
        return list(range(n, 0, -1))
    if cenario == "quase_ordenado":
        # 5% dos elementos trocados de lugar: dados que chegam quase em ordem,
        # como as amostras do monitor, que só saem de ordem por atraso de thread.
        dados = list(range(n))
        for _ in range(max(1, n // 20)):
            i, j = aleatorio.randrange(n), aleatorio.randrange(n)
            dados[i], dados[j] = dados[j], dados[i]
        return dados
    if cenario == "muitos_repetidos":
        # Só 10 valores distintos: muitas chaves iguais.
        return [aleatorio.randrange(10) for _ in range(n)]
    raise ValueError(cenario)


CENARIOS = ("aleatorio", "ordenado", "invertido", "quase_ordenado", "muitos_repetidos")


# ------------------------------------------------------------------------ medida


def medir(funcao, entrada, repeticoes):
    """Aquecimento descartado + N medições. Devolve (mediana, mínimo, máximo)."""
    funcao(entrada)
    tempos = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        funcao(entrada)
        tempos.append(time.perf_counter() - inicio)
    return statistics.median(tempos), min(tempos), max(tempos)


def formatar(segundos):
    if segundos is None:
        return "—"
    if segundos < 0.001:
        return f"{segundos * 1e6:.0f} µs"
    if segundos < 1:
        return f"{segundos * 1000:.1f} ms"
    return f"{segundos:.2f} s"


# -------------------------------------------------------------------- ordenação


def comparar_ordenacao(repeticoes):
    escrever("## Ordenação")
    escrever()
    escrever(
        "Tempo mediano por execução. O mesmo vetor é dado a todos os algoritmos "
        "de cada linha, e a saída de cada um é conferida contra o `sorted()`."
    )

    for cenario in CENARIOS:
        escrever(f"\n### Cenário: `{cenario}`\n")
        cabecalho = "| n | " + " | ".join(nome for nome, _, _ in ALGORITMOS) + " |"
        escrever(cabecalho)
        escrever("|---:|" + "---:|" * len(ALGORITMOS))

        for n in TAMANHOS:
            aleatorio = random.Random(SEMENTE)
            entrada = gerar(cenario, n, aleatorio)
            esperado = sorted(entrada)
            celulas = []
            for nome, funcao, quadratico in ALGORITMOS:
                if quadratico and n > TETO_QUADRATICOS:
                    celulas.append("—")
                    continue
                if funcao(entrada) != esperado:
                    celulas.append("**ERRADO**")
                    medicoes.append([cenario, n, nome, "incorreto", "", ""])
                    continue
                mediana, minimo, maximo = medir(funcao, entrada, repeticoes)
                celulas.append(formatar(mediana))
                resultados[(cenario, n, nome)] = mediana
                medicoes.append([cenario, n, nome, f"{mediana:.6f}", f"{minimo:.6f}", f"{maximo:.6f}"])
            escrever(f"| {n} | " + " | ".join(celulas) + " |")

    escrever(
        f"\n`—` = não medido: acima de {TETO_QUADRATICOS} elementos os O(n²) levariam "
        "minutos e a conclusão já está clara nos tamanhos menores."
    )


# ------------------------------------------------------------------ estabilidade


def comparar_estabilidade():
    escrever("\n## Estabilidade\n")
    escrever(
        "Um algoritmo é **estável** quando elementos de mesma chave saem na ordem "
        "em que entraram. É por isso que a linha do tempo do monitor usa merge "
        "sort: eventos no mesmo instante mantêm a ordem em que aconteceram."
    )
    escrever()

    # Pares (chave, posição de entrada). A ordenação usa SÓ a chave: se o
    # desempate fosse pela posição, todo algoritmo pareceria estável.
    aleatorio = random.Random(SEMENTE)
    entrada = [(aleatorio.randrange(5), i) for i in range(200)]
    so_a_chave = lambda par: par[0]  # noqa: E731

    def esta_estavel(saida):
        ultima_posicao = {}
        for chave, posicao in saida:
            if chave in ultima_posicao and posicao < ultima_posicao[chave]:
                return False
            ultima_posicao[chave] = posicao
        return True

    teoria = {"insertion": "sim", "selection": "não", "merge": "sim", "quick": "não", "sorted (C)": "sim"}
    escrever("| Algoritmo | Estável na prática | Esperado pela teoria |")
    escrever("|---|---|---|")
    for nome, funcao, _ in ALGORITMOS:
        if nome == "sorted (C)":
            saida = sorted(entrada, key=so_a_chave)
        else:
            saida = funcao(entrada, chave=so_a_chave)
        estavel = esta_estavel(saida)
        escrever(f"| {nome} | {'sim' if estavel else 'não'} | {teoria[nome]} |")
        medicoes.append(["estabilidade", 200, nome, "sim" if estavel else "nao", "", ""])


# ------------------------------------------------------------------------ busca


def comparar_busca(repeticoes):
    escrever("\n## Busca\n")
    escrever(
        "Tempo médio por consulta, com 200 alvos sorteados (metade existe no "
        "vetor, metade não). A binária exige vetor ordenado; a sequencial, não."
    )
    escrever()
    escrever("| n | sequencial | binária | binária é ... | ordenar antes (merge) |")
    escrever("|---:|---:|---:|---:|---:|")

    for n in TAMANHOS_BUSCA:
        aleatorio = random.Random(SEMENTE)
        vetor = sorted(aleatorio.randrange(n * 10) for _ in range(n))
        alvos = [aleatorio.choice(vetor) for _ in range(100)] + [
            -1 - aleatorio.randrange(1000) for _ in range(100)
        ]

        def rodar_sequencial(_):
            for alvo in alvos:
                busca_sequencial(vetor, alvo)

        def rodar_binaria(_):
            for alvo in alvos:
                busca_binaria(vetor, alvo)

        sequencial, _, _ = medir(rodar_sequencial, None, repeticoes)
        binaria, _, _ = medir(rodar_binaria, None, repeticoes)
        desordenado = [aleatorio.randrange(n * 10) for _ in range(n)]
        ordenar, _, _ = medir(merge_sort, desordenado, max(1, repeticoes // 2))

        por_consulta_seq = sequencial / len(alvos)
        por_consulta_bin = binaria / len(alvos)
        vezes = por_consulta_seq / por_consulta_bin if por_consulta_bin else 0
        escrever(
            f"| {n} | {formatar(por_consulta_seq)} | {formatar(por_consulta_bin)} | "
            f"{vezes:.0f}× mais rápida | {formatar(ordenar)} |"
        )
        medicoes.append(["busca_sequencial", n, "sequencial", f"{por_consulta_seq:.9f}", "", ""])
        medicoes.append(["busca_binaria", n, "binaria", f"{por_consulta_bin:.9f}", "", ""])

        # Ponto de equilíbrio: a binária só compensa se o vetor já estiver
        # ordenado, ou se houver consultas suficientes para pagar a ordenação.
        economia = por_consulta_seq - por_consulta_bin
        if economia > 0:
            equilibrio = ordenar / economia
            escrever(
                f"| | | | | ordenar compensa a partir de ~{equilibrio:,.0f} consultas |".replace(",", ".")
            )


# ------------------------------------------------------------------------- saída


def _razao(a, b):
    """Quantas vezes `a` é mais lento que `b`, ou None se faltar medição."""
    if a not in resultados or b not in resultados or not resultados[b]:
        return None
    return resultados[a] / resultados[b]


def interpretar():
    """
    Conclusões com os números desta execução. Tudo aqui é calculado a partir das
    medições, e não escrito à mão: rodando em outra máquina, o texto acompanha.
    """
    escrever("\n## Interpretação\n")

    maior_quadratico = TETO_QUADRATICOS
    maior = TAMANHOS[-1]

    razao = _razao(("aleatorio", maior_quadratico, "selection"), ("aleatorio", maior_quadratico, "merge"))
    if razao:
        escrever(
            f"**1. A diferença entre O(n²) e O(n log n) aparece cedo.** Com apenas "
            f"{maior_quadratico} elementos aleatórios, o selection sort levou **{razao:.0f}×** o tempo "
            f"do merge sort. Como a distância cresce com n, nos tamanhos de 5000 e "
            f"{maior} os quadráticos nem foram medidos: levariam minutos."
        )

    rapidez = _razao(("aleatorio", maior_quadratico, "insertion"), ("ordenado", maior_quadratico, "insertion"))
    quase = _razao(("aleatorio", maior_quadratico, "insertion"), ("quase_ordenado", maior_quadratico, "insertion"))
    if rapidez and quase:
        escrever(
            f"\n**2. O melhor caso do insertion sort é real, não detalhe de tabela.** Com "
            f"{maior_quadratico} elementos já ordenados ele foi **{rapidez:.0f}×** mais rápido que com os "
            f"mesmos elementos embaralhados, e **{quase:.0f}×** mais rápido no cenário quase "
            f"ordenado. É por isso que ele é usado dentro do quick sort nos trechos "
            f"com menos de 16 elementos: ali a entrada já está quase em ordem."
        )

    def espalhamento(algoritmo):
        tempos = [
            resultados.get((cenario, maior_quadratico, algoritmo))
            for cenario in CENARIOS
            if resultados.get((cenario, maior_quadratico, algoritmo))
        ]
        return (max(tempos) / min(tempos)) if len(tempos) >= 2 else None

    selection_espalha = espalhamento("selection")
    insertion_espalha = espalhamento("insertion")
    if selection_espalha and insertion_espalha:
        escrever(
            f"\n**3. O selection sort é o que menos depende da ordem da entrada.** Entre os "
            f"cinco cenários, o mais lento foi **{selection_espalha:.1f}×** o mais rápido, contra "
            f"**{insertion_espalha:.0f}×** do insertion sort. Ele faz o mesmo número de comparações "
            f"em qualquer entrada, sempre percorrendo o resto do vetor para achar o "
            f"menor; o que varia é só quantas vezes ele troca o candidato pelo caminho. "
            f"Por isso é o único sem melhor caso: O(n²) sempre."
        )

    ordenado = _razao(("ordenado", maior, "quick"), ("aleatorio", maior, "quick"))
    if ordenado:
        comparacao = (
            f"foi até **{1 / ordenado:.1f}× mais rápido** que com a entrada aleatória"
            if ordenado < 1
            else f"ficou em **{ordenado:.1f}×** o tempo da entrada aleatória"
        )
        escrever(
            f"\n**4. O pivô mediana de três protege o quick sort da entrada ordenada.** O "
            f"pior caso clássico (pivô sempre na ponta, que degenera para O(n²)) não "
            f"apareceu: com {maior} elementos já ordenados, ele {comparacao}."
        )

    repetidos = _razao(("muitos_repetidos", maior, "quick"), ("muitos_repetidos", maior, "merge"))
    cresce = _razao(("muitos_repetidos", maior, "quick"), ("muitos_repetidos", 5000, "quick"))
    if repetidos and repetidos > 3:
        escrever(
            f"\n**5. O pior caso do nosso quick sort não é a entrada ordenada: são as chaves "
            f"repetidas.** Com {maior} elementos e só 10 valores distintos, ele levou "
            f"**{repetidos:.0f}×** o tempo do merge sort"
            + (f", e passar de 5000 para {maior} elementos (4× mais dados) multiplicou o tempo "
               f"por **{cresce:.0f}×**, o que é comportamento quadrático, não O(n log n)." if cresce else ".")
        )
        escrever(
            "\n   A causa é a partição de Lomuto: ela separa em \"vem antes do pivô\" e "
            "\"não vem\", então todos os elementos iguais ao pivô caem do mesmo lado. Com "
            "muitas chaves iguais, cada partição tira só um elemento e a recursão vira "
            "O(n²). A correção conhecida é a partição em três vias (menor, igual, maior), "
            "que joga todos os iguais para o meio de uma vez."
        )
        escrever(
            "\n   **Onde isso nos afeta hoje:** o monitor usa quick sort para ordenar "
            "processos e containers por CPU ou memória. São dezenas de elementos, e as "
            "chaves são percentuais com casas decimais, que quase nunca empatam, então o "
            "caso ruim não acontece ali. O cuidado vale para qualquer uso futuro com "
            "muitos valores repetidos (ordenar por status ou por severidade, por exemplo), "
            "onde o merge sort é a escolha segura."
        )

    escrever(
        "\n**6. O `sorted()` do Python é régua, não concorrente.** Ele é Timsort em C, e os "
        "nossos são Python interpretado: a diferença de uma a duas ordens de grandeza é "
        "do interpretador, não dos algoritmos. O que importa na comparação é o formato "
        "da curva de cada um, e esse bate com a teoria."
    )


def main():
    argumentos = argparse.ArgumentParser(description="Benchmark dos algoritmos próprios (PI3).")
    argumentos.add_argument("--rapido", action="store_true", help="menos repetições (resultado mais ruidoso)")
    opcoes = argumentos.parse_args()
    repeticoes = 3 if opcoes.rapido else 5

    escrever("# Benchmark entre algoritmos — PI3")
    escrever()
    escrever(f"Gerado em {datetime.now().astimezone():%d/%m/%Y %H:%M:%S (UTC%z)}")
    escrever()
    # A máquina muda os números, então ela faz parte do resultado: sem isto, a
    # tabela não é reproduzível nem comparável com outra execução.
    escrever(
        f"**Máquina:** {platform.processor() or platform.machine()}, "
        f"{os.cpu_count()} CPUs lógicas · {platform.system()} {platform.release()} · "
        f"Python {sys.version.split()[0]}"
    )
    escrever(
        f"**Método:** semente {SEMENTE} (mesma entrada em toda execução) · "
        f"1 aquecimento descartado + {repeticoes} medições por configuração · mediana"
    )
    escrever()

    relogio = time.perf_counter()
    comparar_ordenacao(repeticoes)
    comparar_estabilidade()
    comparar_busca(repeticoes)
    interpretar()
    escrever(f"\nTempo total do benchmark: {time.perf_counter() - relogio:.0f}s")

    SAIDA_MD.parent.mkdir(parents=True, exist_ok=True)
    SAIDA_MD.write_text("\n".join(linhas_md) + "\n", encoding="utf-8")
    with open(SAIDA_CSV, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(["cenario", "n", "algoritmo", "mediana_s", "minimo_s", "maximo_s"])
        escritor.writerows(medicoes)
    print(f"\nSalvo em {SAIDA_MD.relative_to(RAIZ)} e {SAIDA_CSV.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
