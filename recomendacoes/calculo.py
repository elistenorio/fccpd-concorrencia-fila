"""
Cálculo de recomendações "quem comprou isto também comprou" — o TRECHO
PARALELO da Parte 2 da FCCPD.

Para cada produto A, compara com todos os outros produtos B:

    score(A, B) = Jaccard(compradores de A, compradores de B)
                  × (1 + BONUS_CATEGORIA  se A e B são da mesma categoria
                       + BONUS_ARTESAO    se A e B são do mesmo artesão)

    Jaccard = |A ∩ B| / |A ∪ B|  (0 = nenhum cliente em comum, 1 = mesmos clientes)

O bônus multiplica em vez de somar: um bônus somado (+0,1) valia mais que a
própria similaridade (Jaccard típico entre 0,01 e 0,05), e o resultado virava
"do mesmo artesão" em vez de "quem comprou também comprou". Multiplicando, o
bônus só reforça produtos que já têm compradores em comum.

e guarda os K de maior score (TopK, estrutura própria). São n² comparações,
todas independentes e só de CPU: o caso ideal para paralelizar.

Como paralelizamos: os produtos são divididos em blocos contíguos e cada
worker calcula as recomendações de um bloco inteiro. Os dados (compradores,
categorias, artesãos) vão para cada processo UMA vez, no initializer do pool,
e não a cada bloco. Cada bloco devolve só o seu top-K, então o que volta
pelos processos é pequeno.

Três modos com o mesmo código (``calcular_bloco``), para comparar no benchmark:
- sequencial: um bloco só, no processo atual;
- threads:    ThreadPoolExecutor — o GIL deixa só uma thread executar Python
              por vez, então NÃO deve ganhar em trabalho de CPU;
- processos:  ProcessPoolExecutor — cada processo tem o seu interpretador e o
              seu GIL, então os blocos rodam de fato ao mesmo tempo em núcleos
              diferentes.

Este módulo não importa banco nem FastAPI: os processos filhos só carregam o
necessário para calcular.
"""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from estruturas.top_k import TopK

BONUS_CATEGORIA = 0.2
BONUS_ARTESAO = 0.1
MODOS = ("sequencial", "threads", "processos")

# Dados do cálculo, preenchidos por preparar(). Em modo processos, cada
# processo filho tem a sua cópia, recebida uma vez pelo initializer.
_compradores = None
_categorias = None
_artesaos = None


def preparar(compradores, categorias, artesaos):
    global _compradores, _categorias, _artesaos
    _compradores = compradores
    _categorias = categorias
    _artesaos = artesaos


def calcular_bloco(inicio, fim, k):
    """Top-K de cada produto i em [inicio, fim). Devolve lista de [(j, score), ...]."""
    compradores, categorias, artesaos = _compradores, _categorias, _artesaos
    n = len(compradores)
    tamanhos = [len(c) for c in compradores]
    resultado = []
    for i in range(inicio, fim):
        a, tam_a = compradores[i], tamanhos[i]
        cat_a, art_a = categorias[i], artesaos[i]
        top = TopK(k)
        for j in range(n):
            if j == i:
                continue
            comum = len(a & compradores[j])
            if comum == 0:
                continue
            score = comum / (tam_a + tamanhos[j] - comum)
            fator = 1.0
            if categorias[j] == cat_a:
                fator += BONUS_CATEGORIA
            if artesaos[j] == art_a:
                fator += BONUS_ARTESAO
            score *= fator
            if score > top.limiar:
                top.oferecer(j, score)
        resultado.append(top.resultado())
    return resultado


def dividir_em_blocos(n, n_blocos):
    """Intervalos [inicio, fim) contíguos e de tamanhos quase iguais."""
    n_blocos = max(1, min(n_blocos, n))
    base, sobra = divmod(n, n_blocos)
    blocos, inicio = [], 0
    for b in range(n_blocos):
        fim = inicio + base + (1 if b < sobra else 0)
        blocos.append((inicio, fim))
        inicio = fim
    return blocos


def calcular(compradores, categorias, artesaos, modo="processos", workers=4, k=10, blocos_por_worker=4):
    """
    Recomendações de todos os produtos, na mesma ordem das listas de entrada.

    blocos_por_worker > 1 equilibra a carga: produtos populares (muitos
    compradores) custam mais, e com vários blocos menores um worker que
    terminou cedo pega o próximo em vez de ficar parado.
    """
    if modo not in MODOS:
        raise ValueError(f"modo deve ser um de {MODOS}")
    n = len(compradores)

    if modo == "sequencial":
        preparar(compradores, categorias, artesaos)
        return calcular_bloco(0, n, k)

    blocos = dividir_em_blocos(n, workers * blocos_por_worker)
    inicios = [b[0] for b in blocos]
    fins = [b[1] for b in blocos]
    ks = [k] * len(blocos)

    if modo == "threads":
        preparar(compradores, categorias, artesaos)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            partes = pool.map(calcular_bloco, inicios, fins, ks)
            return [recs for parte in partes for recs in parte]

    # "spawn" cria processos novos em vez de copiar (fork) o processo do
    # servidor, que já tem threads rodando; fork de processo com threads pode
    # travar e é desaconselhado desde o Python 3.12. Custa uns décimos de
    # segundo para subir os processos, o que aparece no benchmark com poucos dados.
    contexto = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=contexto,
        initializer=preparar,
        initargs=(compradores, categorias, artesaos),
    ) as pool:
        partes = pool.map(calcular_bloco, inicios, fins, ks)
        return [recs for parte in partes for recs in parte]
