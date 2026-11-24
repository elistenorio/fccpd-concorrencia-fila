# Origem

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-RQ-DC382D?style=flat-square&logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white)

Backend do marketplace de artesanato Origem, projeto integrado de três
disciplinas: [**FCCPD**](#fccpd-parte-2-u2--arquitetura-distribuída-e-desempenho),
[**AED**](#aed) e [**Infraestrutura de Software**](#infraestrutura-de-software).

**FCCPD:** Aguinaldo Neto, Caliel Feijó, Elis Tenório, Eulália Albuquerque, Giulia Ferreira e Sarah Cyrne  
**AED e Infraestrutura de Software:** Caliel Feijó, Eulália Albuquerque e Giulia Ferreira

## Como rodar

```bash
docker compose up -d --build --wait
docker compose exec api python -m app.seed      # histórico de compras para as recomendações
curl -X POST "http://localhost:8001/recomendacoes/recalcular?modo=processos"
```

Sobe 9 containers: `postgres`, `redis`, `api` (`:8000`), 3 réplicas de
`worker`, `reconciliador`, `recomendacoes` (`:8001`) e `monitor` (`:8002`). O
seed cria 2000 produtos, 10 mil clientes e 80 mil pedidos (apaga tudo, menos o
produto 1 dos testes).

**Painel de monitoramento do PI3: <http://localhost:8002/painel/>**

## Endpoints

| Serviço | Rota | O que faz |
|---|---|---|
| api | `POST /pedidos` | Cria pedido (`{"produto_id", "cliente_id"?}`): **201**, **409** sem estoque, **404** produto inexistente |
| api | `GET /pedidos/{id}` | Status: `pendente`, `processado` ou `falhou` |
| api | `GET /produtos/{id}?limite=5` | Produto + recomendações (se o serviço cair: `"recomendacoes_disponiveis": false`) |
| recomendacoes | `GET /recomendacoes/{id}?limite=5` | Recomendações pré-calculadas |
| recomendacoes | `POST /recomendacoes/recalcular?modo=processos&workers=4` | Recalcula tudo (`sequencial`, `threads` ou `processos`); **409** se já houver recálculo |

## FCCPD Parte 2 (U2) — Arquitetura distribuída e desempenho

| Critério | Item verificado | Onde |
|---|---|---|
| 1. Arquitetura distribuída | Diagrama | [Diagrama](#diagrama) |
| | Separação justificada | [Por que cada serviço é separado](#por-que-cada-serviço-é-separado) |
| | Comunicação implementada e demonstrada | [Comunicação](#comunicação-entre-os-serviços) · [`evidencias/u2/comunicacao.txt`](evidencias/u2/comunicacao.txt) · CI |
| 2. Paralelismo com ganho | Processamento paralelo implementado | [`recomendacoes/calculo.py`](recomendacoes/calculo.py) · [Paralelismo](#paralelismo-recálculo-de-recomendações) |
| | Ganho comprovado com números | [Resultados](#resultados) |
| 3. Avaliação de desempenho | Metodologia clara | [Metodologia](#metodologia) |
| | Resultados interpretados | [Interpretação](#interpretação) |

### Diagrama

```mermaid
flowchart LR
    cliente([Cliente])
    admin([Rotina / admin])

    subgraph compose[Docker Compose]
        api["api :8000<br/>checkout e página do produto"]
        rec["recomendacoes :8001<br/>consulta + recálculo paralelo"]
        worker["worker ×3<br/>pós-venda (RQ)"]
        recon["reconciliador"]
        redis[("Redis<br/>fila 'pedidos'")]
        pg[("PostgreSQL")]
    end

    cliente -- "POST /pedidos<br/>GET /produtos/{id}" --> api
    admin -- "POST /recomendacoes/recalcular" --> rec
    api == "HTTP/REST síncrono<br/>GET /recomendacoes/{id}<br/>prazo 1 s + disjuntor" ==> rec
    api == "publica job pedido-{id}<br/>assíncrono" ==> redis
    redis == "consome job" ==> worker
    recon -- "republica pedidos sem job" --> redis
    api -- "SELECT ... FOR UPDATE<br/>grava pedido" --> pg
    worker -- "marca processado" --> pg
    recon -- "lê pendentes" --> pg
    rec -- "lê pedidos e produtos<br/>grava recomendacoes" --> pg
```

Setas grossas: comunicação entre serviços (uma síncrona, uma assíncrona). Setas finas: acesso ao banco e à fila.

### Por que cada serviço é separado

| Serviço | Responsabilidade | Por que existe separado |
|---|---|---|
| `api` | Checkout (baixa de estoque com lock), pedidos, página do produto | Caminho crítico das vendas: precisa responder rápido |
| `worker` (×3) | Pós-venda (simula nota fiscal, 3 s) | Trabalho lento fora da requisição; escala com réplicas e tem retry (Parte 1) |
| `reconciliador` | Republica pedidos que ficaram sem job | Laço periódico; dentro da api rodaria duplicado em cada réplica |
| `recomendacoes` | Consulta e **recálculo** das recomendações | O recálculo (4 milhões de comparações) ocupa todos os núcleos por segundos. Separado, não deixa o checkout lento nem o derruba se falhar, e escala sozinho |
| `postgres`, `redis` | Banco e broker | Infraestrutura compartilhada |

**O que não separamos:** o banco é compartilhado (o `recomendacoes` só escreve
na própria tabela), e a `api` não foi dividida em "pedidos" e "catálogo":
seria distribuição sem ganho, risco que o próprio enunciado aponta.

### Comunicação entre os serviços

| De → para | Tipo | Como | Por quê |
|---|---|---|---|
| `api` → `recomendacoes` | Síncrona (HTTP/REST) | `GET /recomendacoes/{id}` em [`app/recomendacoes_cliente.py`](app/recomendacoes_cliente.py) | A página do produto precisa da resposta na hora |
| `api` → `worker` | Assíncrona (Redis/RQ) | job `pedido-<id>` na fila `pedidos` | O cliente não espera o pós-venda |

A recomendação é um extra e não pode derrubar a página:
- **Prazo de 1 s** (com o container parado, só o DNS levava 7,2 s e ignorava o timeout do httpx).
- **Disjuntor** de 15 s depois de uma falha e **fallback** com `"recomendacoes_disponiveis": false`.
- **`X-Request-ID`** em cada chamada, visível no log dos dois serviços.

**Evidência** (trecho de [`evidencias/u2/comunicacao.txt`](evidencias/u2/comunicacao.txt),
gerado por [`evidencia_comunicacao.py`](evidencia_comunicacao.py) a partir dos logs reais):

```
GET http://127.0.0.1:8000/produtos/2?limite=3 -> 200  (X-Request-ID: 44f3adb0)
    recomenda  1625  Peça de cerâmica 1624        score 0.0667
    ...
  api-1            | INFO:     [44f3adb0] GET http://recomendacoes:8001/recomendacoes/2 -> 200, 3 recomendações em 32 ms
  recomendacoes-1  | ... INFO [recomendacoes] [44f3adb0] Consulta de recomendações do produto 2: 3 encontradas

POST http://127.0.0.1:8000/pedidos -> 201
  worker-3  | 16:06:06 pedidos: app.tasks.processar_pedido(81057) (pedido-81057)
  worker-3  | 16:06:10 pedidos: Job OK (pedido-81057)

$ docker compose stop recomendacoes
GET /produtos/2 (tentativa 1) -> 200 em 1112 ms, recomendacoes_disponiveis=False
GET /produtos/2 (tentativa 2) -> 200 em 13 ms, recomendacoes_disponiveis=False
GET /produtos/2 (tentativa 3) -> 200 em 9 ms, recomendacoes_disponiveis=False
$ docker compose start recomendacoes
GET /produtos/2 depois que o serviço voltou -> 200, recomendacoes_disponiveis=True
```

O script roda no CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) a
cada push, e o arquivo fica anexado à execução. Para gerar de novo (depois dos
passos de [Como rodar](#como-rodar), nessa ordem): `python evidencia_comunicacao.py`

### Paralelismo: recálculo de recomendações

**Tarefa real:** "quem comprou isto também comprou". Cada produto A é comparado com todos os outros B:

```
score(A, B) = Jaccard(compradores de A, compradores de B) × (1 + 0,2 se mesma categoria + 0,1 se mesmo artesão)
Jaccard     = |clientes em comum| / |clientes de A ou de B|
```

Guarda os 10 maiores. São n·(n−1) comparações independentes e só de CPU (4 milhões com 2000 produtos).

> O bônus multiplica em vez de somar: somando, ele valia mais que a própria
> similaridade e o sistema recomendava "do mesmo artesão".

**Onde está:** [`recomendacoes/calculo.py`](recomendacoes/calculo.py), `calcular()` (reparte) e `calcular_bloco()` (cada worker).

**Como:**
- Produtos em blocos contíguos (4 por worker, para equilibrar a carga).
- **Processos (`ProcessPoolExecutor`), não threads:** o GIL impede threads de acelerar CPU. O modo `threads` existe só para o benchmark provar isso.
- Dados enviados **uma vez** a cada processo (`initializer`); cada bloco devolve só o seu top-10.
- `spawn` em vez de `fork` (fork com threads rodando pode travar; custo medido no experimento D).
- Banco lido só no processo principal ([`recomendacoes/servico.py`](recomendacoes/servico.py)).
- Resultado **idêntico** ao sequencial ([`tests/test_recomendacoes_calculo.py`](tests/test_recomendacoes_calculo.py) e conferido no benchmark).

### Metodologia

| | |
|---|---|
| **O que foi medido** | Tempo de `calcular()`, **incluindo** criar e encerrar o pool. Leitura e gravação no banco ficam de fora (iguais nos três modos; ver item 5). Derivadas: **vazão**, **speedup** e **eficiência** |
| **Como** | [`recomendacoes/benchmark.py`](recomendacoes/benchmark.py), em container isolado da mesma imagem (`docker compose run --no-deps`) |
| **Volume** | 250 a 4000 produtos (62 mil a 16 milhões de comparações), gerados por `app.seed.gerar_dados` com semente 42 |
| **Repetições** | 1 aquecimento descartado + **5 medições**; reportamos **mediana**, mín, máx e desvio |
| **Configurações** | **A:** volume variando, 4 workers. **B:** 2000 produtos, 1 a 8 workers. **C, D, E:** diagnósticos (teto do hardware, custo do pool, disputa entre processos) |
| **Máquina** | Intel Core i5-1035G1 (4 núcleos físicos / 8 lógicos), Windows 11 + Docker Desktop (WSL2), container com 8 CPUs e 3,7 GB, Python 3.12.16 |

```bash
docker compose run --rm --no-deps -v ./evidencias:/code/evidencias recomendacoes python -m recomendacoes.benchmark
```

Saída completa: [`evidencias/u2/benchmark.md`](evidencias/u2/benchmark.md) · medições: [`evidencias/u2/benchmark.csv`](evidencias/u2/benchmark.csv).

### Resultados

**A — efeito do volume** (4 workers; tempo mediano; speedup entre parênteses)

| Produtos | Comparações | Sequencial | Threads ×4 | Processos ×4 |
|---:|---:|---:|---:|---:|
| 250 | 62 mil | 0,046 s | 0,060 s (0,76×) | 0,210 s (**0,22×**) |
| 500 | 250 mil | 0,285 s | 0,276 s (1,03×) | 0,405 s (**0,70×**) |
| 1000 | 1 milhão | 1,165 s | 1,137 s (1,02×) | 1,147 s (1,02×) |
| 2000 | 4 milhões | 3,699 s | 4,088 s (0,90×) | 1,607 s (**2,30×**) |
| 4000 | 16 milhões | 14,014 s | 13,780 s (1,02×) | 4,821 s (**2,91×**) |

**B — efeito do nº de workers** (2000 produtos; sequencial = 4,102 s)

| Workers | Threads | Processos | Eficiência dos processos |
|---:|---:|---:|---:|
| 1 | — | 3,378 s (1,21×) | — |
| 2 | 4,145 s (0,99×) | 2,112 s (1,94×) | 0,97 |
| 4 | 3,714 s (1,10×) | **1,540 s (2,66×)** | 0,67 |
| 6 | — | 1,771 s (2,32×) | 0,39 |
| 8 | 3,762 s (1,09×) | 2,403 s (1,71×) | 0,21 |

**Diagnóstico** (C, D e E)

| Workers | C: speedup de laço puro de CPU (teto) | D: custo de criar/encerrar o pool | E: mesmo bloco com N workers juntos |
|---:|---:|---:|---:|
| 1 | 0,81× | 0,170 s | 0,183 s (1,00×) |
| 2 | 1,12× | 0,169 s | 0,199 s (1,09×) |
| 4 | 2,31× | 0,243 s | 0,338 s (1,85×) |
| 6 | 2,68× | 0,323 s | 0,375 s (2,05×) |
| 8 | 2,34× | 0,536 s | 0,432 s (2,36×) |

**Resumo:** com 2000 produtos, de **3,7–4,1 s para 1,5–1,6 s** com 4 processos
(**2,3× a 2,7×**); com 4000, de **14,0 s para 4,8 s (2,9×)**. Threads não ganharam em nenhum caso.

### Interpretação

1. **Threads não ganham (0,76× a 1,10×): é o GIL.** Só uma thread executa Python por vez; isso confirma a escolha por processos.
2. **Pouco volume, paralelizar piora** (0,22× com 250 produtos): criar 4 processos (~0,24 s) custa mais que o cálculo (0,046 s). Equilíbrio perto de **1000 produtos**; abaixo disso, use `modo=sequencial`.
3. **O ganho cresce com o volume** (2,30× → 2,91×): o trabalho cresce com n² e o custo do pool é fixo.
4. **Melhor com 4 workers** (= núcleos físicos); com 6 e 8 piora, por três causas medidas:
   - hyperthreading não é núcleo de verdade (teto de ~2,3–2,7×, exp. C);
   - disputa por cache L3 e memória (o mesmo bloco fica 1,85× mais lento com 4 juntos e 2,36× com 8, exp. E);
   - custo do pool sobe de 0,17 s para 0,54 s (exp. D).

   **Decisão:** o padrão passou de `os.cpu_count()` (8) para **4** workers, configurável em `RECOMENDACOES_WORKERS` no [`docker-compose.yml`](docker-compose.yml).
5. **Lei de Amdahl:** leitura (~0,3–0,7 s) e gravação (~0,8–0,9 s) continuam sequenciais. O cálculo caiu 2,8× (6,1 → 2,2 s), mas o recálculo inteiro caiu 2,4× (7,8 → 3,3 s). A gravação fica numa única transação para ninguém ver a tabela pela metade.
6. **Variação da máquina:** o notebook reduz a frequência quando esquenta (até ~15% entre rodadas), por isso usamos mediana e publicamos mín/máx. O "1 processo = 1,21×" está dentro desse ruído; os ganhos de 2,3× a 2,9× não.
7. **Relação com a arquitetura:** o recálculo ocupa todos os núcleos, por isso fica fora da `api`. Em produção, iria para uma máquina própria ou com limite de CPU, sem tocar na `api`.

## FCCPD Parte 1 — Concorrência no checkout e fila assíncrona

**Critério 1 — Controle de concorrência.** Lock pessimista no `produto`
(`SELECT ... FOR UPDATE` via `with_for_update()`): outra transação que tente ler
a mesma linha espera o `COMMIT`/`ROLLBACK`. Escolhemos pessimista porque o
Origem vende peças únicas e a disputa é curta e pontual; lock otimista exigiria
retry no cliente sem ganho real.

**Critério 2 — Consistência de estoque.** Provada pelo [teste de concorrência](#testes-e-evidências):
50 requisições simultâneas contra 10 unidades, conferidas pelas respostas HTTP e pelo PostgreSQL.

**Critério 3 — Publicação de eventos (produtor).** O pedido nasce `pendente`, a
API responde **201** na hora e só depois publica um job na fila `pedidos`
(`publicar_pedido` em `app/queue.py`):
- id fixo por pedido (`pedido-<id>`), sem duplicidade;
- tarefa referenciada pelo caminho (`"app.tasks.processar_pedido"`): a API não importa o código do worker;
- retry automático (`Retry(max=3, interval=[5, 15, 30])`).

**Critério 4 — Desacoplamento e integridade (consumidor).** O `worker`
(`app/worker.py`, 3 réplicas) consome a fila em outro processo e marca o pedido
como `processado`. A compra é gravada no banco antes de ir para a fila, então
um Redis lento ou fora do ar não derruba vendas. Nenhum pedido se perde:

| O que falha | O que acontece |
|---|---|
| Redis fora na compra | Responde 201 e o pedido fica `pendente`; o **reconciliador** republica (a cada 5 s, pendentes há mais de 10 s). Disjuntor de 15 s na API |
| Redis reinicia com jobs | `--appendonly yes` grava a fila em disco; o resto é coberto pelo reconciliador |
| Job falha | 3 tentativas (5, 15, 30 s); depois, `marcar_pedido_como_falho` marca `falhou` |
| Worker morre no meio | O RQ detecta o job abandonado e aplica o retry |
| Pedido chega duas vezes | Tarefa idempotente (UPDATE condicional): a nota não sai duas vezes |

### Testes e evidências

| Teste | Comando (containers de pé) | Resultado |
|---|---|---|
| Concorrência | `pip install httpx` e `python test_concorrencia.py --controle-negativo` | 10 vendidos, 40 recusados, estoque 0. Sem o lock: 49 vendidos para 10 unidades |
| Fila | `docker compose cp test_fila.py api:/code/test_fila.py` e `docker compose exec api python test_fila.py` | 5 pedidos `pendente` em até 54 ms; todos `processado` em 10,1 s |
| Resiliência | `python test_resiliencia.py` | Com o Redis fora: 5 compras com 201 e nenhuma perdida (antes: HTTP 500 após ~7 s) |
| Bateria completa | `python gerar_evidencias.py` | Gera a pasta [`evidencias/`](evidencias/RESUMO.md) (5 rodadas de concorrência, fila, resiliência, logs e ambiente) |

<details>
<summary><b>Ver os relatórios completos dos testes da Parte 1</b></summary>

#### Concorrência (Critério 5)

O script reseta o produto 1 com 10 unidades, dispara 50 `POST /pedidos`
simultâneos, confere estoque e pedidos via `docker compose exec postgres psql`
e falha se algo não bater. Com `--controle-negativo`, repete o ataque antes
contra uma API temporária (porta 8001) com o `with_for_update()` desligado só nela.

```
=== CONTROLE NEGATIVO: MESMO ATAQUE CONTRA A API SEM LOCK ===
Sucessos (201 Created): 49 | Conflitos (409): 1 | Outros: 0
Estoque Final no PostgreSQL: 0
Pedidos Criados no PostgreSQL: 49
-> Sem o lock: 39 unidade(s) vendida(s) além do estoque e 39 baixa(s) de estoque perdida(s) (lost update).
-> O teste DETECTA a condição de corrida quando o lock é removido.

=== RELATÓRIO DE TESTE DE CONCORRÊNCIA ===
Estoque Inicial: 10
Requisições Simultâneas: 50

--- RESULTADOS HTTP ---
Sucessos (201 Created): 10
Conflitos (409 Conflict): 40
Erros Inesperados: 0

--- VERIFICAÇÃO NO BANCO DE DADOS ---
Estoque Final no PostgreSQL: 0
Pedidos Criados no PostgreSQL: 10

✅ VEREDITO: O sistema manteve a consistência sob alta concorrência. Zero erros.
```

O controle negativo prova que o teste detecta a condição de corrida: o
resultado perfeito vem do lock, e não de sorte.

#### Fila (Critérios 3 e 4)

Dispara 5 pedidos, confere 201 com `pendente` em menos de 500 ms (cada job
leva 3 s) e espera todos virarem `processado` em até 30 s.

```
=== RELATÓRIO DE TESTE DA FILA ===
Pedidos criados: 5 (todos responderam 201 com status 'pendente')
Latência do POST /pedidos: mín 29 ms | mediana 43 ms | máx 54 ms (cada job leva 3000 ms)
Todos processados pelos workers em 10.1s (limite: 30s)

[OK] 5 pedidos criados, todos processados pelo worker em até 30s, sem a compra esperar pelo processamento.
```

#### Resiliência — Redis fora do ar (Critério 4)

Derruba o Redis, faz 5 compras, confere 201 em menos de 1 s, pedidos
`pendente` e estoque baixado; religa o Redis (sempre) e espera todos virarem `processado`.

```
=== RELATÓRIO DE TESTE DE RESILIÊNCIA (REDIS FORA DO AR) ===
Compras feitas com o Redis desligado: 5

--- COM O REDIS FORA ---
Respostas 201 Created: 5 | Outras: []
Latência do POST /pedidos: mediana 77 ms | máx 107 ms
Pedidos 'pendente' no PostgreSQL: 5
Estoque no PostgreSQL: 10 -> 5

--- DEPOIS DE RELIGAR O REDIS ---
Pedidos 'processado': 5 de 5 em 16.1s (republicados pelo reconciliador)

✅ VEREDITO: Com o Redis fora, a loja continuou vendendo e nenhum pedido se perdeu.
```

</details>

## AED

<details>
<summary><b>Clique para ver a parte de AED</b> — estruturas de dados, ordenação e busca próprias (<code>estruturas/</code>)</summary>

<br>

**Regra:** classes com nós encadeados e blocos de tamanho fixo `[None] * n` como
array. Nas partes avaliadas não usamos `list.append/pop/sort`, `dict`, `set`,
`deque`, `sorted` nem `bisect`.

| Técnica | Onde | Por quê | Complexidade | Status |
|---|---|---|---|---|
| Lista encadeada | `estruturas/lista.py` (usada em `ordenacao.py`) | Copiar entrada de tamanho desconhecido numa passada | início/fim O(1); busca O(n) | `pronto` |
| Pilha | `estruturas/pilha.py` (usada em `arvore_busca.py`) | Percorrer a árvore sem recursão (evita estouro no caso degenerado) | O(1) | `pronto` |
| Fila | `estruturas/fila.py` (usada em `monitor/estado.py`) | Fila de alertas: uma amostra pode ser descartada pela janela, um alerta não, então a fila não tem limite | O(1) | `pronto` |
| Fila circular | `estruturas/fila_circular.py` (usada em `concorrencia/buffer.py` e `monitor/estado.py`) | Buffer entre as threads do coletor e janela das últimas 300 amostras de cada nível: o monitor roda por horas sem a memória crescer | O(1); memória O(capacidade) | `pronto` |
| Tabela hash | `estruturas/tabela_hash.py` (usada em `monitor/estado.py`) | Acesso por chave: estado por container e por serviço no painel, clientes por produto nas recomendações | O(1) médio, O(n) pior; redimensiona em 0,75 | `pronto` |
| Árvore binária de busca | `estruturas/arvore_busca.py` (`itens_no_intervalo`, usada em `monitor/estado.py`) | "Eventos dos últimos 15 min": poda as subárvores fora do intervalo. Horários crescentes degenerariam a árvore, então ela é reconstruída balanceada quando a altura passa do dobro do ideal | intervalo O(h + k); h ~log n pelo rebalanceamento, O(n) amortizado | `pronto` |
| Insertion / selection sort | `estruturas/ordenacao.py` | Base de comparação; insertion também no quick sort (< 16 elementos) | O(n²) (insertion O(n) quase ordenado) | `pronto` |
| Top-K | `estruturas/top_k.py` (usado em `recomendacoes/calculo.py`) | Top-10 entre ~2000 candidatos sem ordenar todos | O(n·k) pior, ~O(n) na prática | `pronto` |
| Merge sort | `estruturas/ordenacao.py` (usado em `monitor/estado.py`) | Linha do tempo por horário; estável, então eventos do mesmo instante mantêm a ordem em que aconteceram | O(n log n); memória O(n) | `pronto` |
| Quick sort | `estruturas/ordenacao.py` (usado em `monitor/nivel1_so.py` e `monitor/estado.py`) | Processos por CPU (a amostra guarda os 25 maiores) e containers por CPU, memória ou nome; pivô mediana de três | O(n log n) médio | `pronto` |
| Busca sequencial / binária | `estruturas/busca.py` (usadas em `monitor/estado.py`) | Sequencial para achar um processo pelo nome (amostra ordenada por CPU); binária para o corte do histórico por horário | O(n) / O(log n) | `pronto` |

`pronto` = implementado, em uso e coberto por testes. Todas as estruturas da
disciplina são usadas pelo produto: as da FCCPD no cálculo de recomendações e as
demais no monitor ([Infraestrutura de Software](#infraestrutura-de-software)).

**Testes** (sem Docker): [`tests/test_estruturas.py`](tests/test_estruturas.py) e [`tests/test_ordenacao_busca.py`](tests/test_ordenacao_busca.py)

```bash
pip install -r requirements-dev.txt
pytest tests/test_estruturas.py tests/test_ordenacao_busca.py
pytest tests/test_monitor_estado.py   # as estruturas em uso no monitor
```

</details>

## Infraestrutura de Software

<details>
<summary><b>Clique para ver a parte de Infraestrutura de Software</b> — threads, sincronização, condição de corrida e monitor (<code>concorrencia/</code>, <code>scripts/</code>)</summary>

<br>

| Exigência | Como atendemos |
|---|---|
| Linguagem | Python 3.12, porque o app monitorado (este backend) já é Python |
| Threads e sincronização | `threading` (`Thread`, `Lock`, `Condition`) |
| Comandos do SO ou `/proc` | Leitura de `/proc/stat`, `/proc/meminfo` e `/proc/<pid>/stat`, mais `statvfs`; o Lab 09 é em Bash (`top`, `ps`, `pgrep`) |
| Container Linux e CI | Imagem `python:3.12-slim` (api, recomendações, workers e monitor); pytest no GitHub Actions, mais um job que sobe o Compose inteiro ([`ci.yml`](.github/workflows/ci.yml)) |

**Testes e demonstração** (sem Docker):

```bash
pip install -r requirements-dev.txt
pytest tests/test_produtor_consumidor.py tests/test_monitor_*.py
python -m concorrencia.demo_corrida     # mesma carga sem lock x com lock
```

**GIL:** só uma thread executa Python por vez. Não atrapalha o monitor (passa
o tempo esperando I/O, e o GIL é liberado na espera); para CPU pesado (as
recomendações da FCCPD) usamos processos.

| Técnica | Onde | Por quê | Status |
|---|---|---|---|
| Produtor-consumidor (`Lock` + 2 `Condition`) | `concorrencia/buffer.py` | Produtores dormem com o buffer cheio e consumidores com ele vazio, sem espera ocupada | `pronto` |
| Condição de corrida com threads | `concorrencia/estatisticas.py`, `concorrencia/demo_corrida.py` | "ler → somar → gravar" não é atômico; o `Lock` torna a seção crítica exclusiva | `pronto` |
| Condição de corrida no banco | `app/main.py` (`FOR UPDATE`), `test_concorrencia.py --controle-negativo` | Duas compras do último item; sem lock, vende a mais | `pronto` (FCCPD) |
| Monitor via comandos do SO | `scripts/monitor.sh` | CPU/memória via `top`; RSS, threads e estado via `ps` | `pronto` |
| Coletor concorrente (3 produtoras + 1 consumidora) | `monitor/coletor.py` | Cada nível coleta no seu ritmo; o buffer sincronizado leva as amostras a uma única thread que escreve no estado | `pronto` |
| Coleta de hardware/SO por `/proc` | `monitor/nivel1_so.py` | CPU por núcleo, memória, disco, processos e threads | `pronto` |
| Coleta de containers e serviços | `monitor/nivel2_containers.py`, `monitor/nivel3_servicos.py` | API da Docker Engine (status, CPU, memória, reinícios, OOMKilled) e health check HTTP | `pronto` |
| Painel com as 3 telas | `monitor/web/`, `monitor/main.py` | Hardware/SO, containers e serviços, com alertas e linha do tempo | `pronto` |

**Demonstração** (3 produtores × 2000 itens, 3 consumidores):

```
[SEM lock] total=1945 soma=5659166 perdidas=4055 (67.6%) -> ERRADO (condição de corrida)
[COM lock] total=6000 soma=18003000 perdidas=0 (0.0%)    -> CORRETO
```

Sem lock, o número de perdas muda a cada execução; com lock, é sempre exato. A
seção crítica cede a CPU com `time.sleep(0)` para a corrida aparecer de forma
reproduzível (por causa do GIL ela seria rara); o `Lock` corrige com ou sem o `sleep`.

### Monitoramento: coletor concorrente e painel

O app monitorado é o próprio backend do Origem. O serviço `monitor` sobe no
mesmo Compose, coleta os três níveis com threads e serve o painel em
<http://localhost:8002/painel/>.

```mermaid
flowchart LR
    navegador([Navegador])

    subgraph compose[Docker Compose]
        subgraph monitorado[Aplicação monitorada]
            api["api :8000"]
            rec["recomendacoes :8001"]
            worker["worker ×3 · reconciliador"]
            dados[("PostgreSQL · Redis")]
        end

        subgraph mon["monitor :8002"]
            t1["thread nível 1<br/>/proc, 2 s"]
            t2["thread nível 2<br/>Docker Engine API, 5 s"]
            t3["thread nível 3<br/>health check, 5 s"]
            buffer{{"BufferLimitado<br/>FilaCircular + Lock + 2 Condition"}}
            consumidor["thread consumidora"]
            estado[("EstadoMonitor<br/>hash · fila circular<br/>árvore · fila")]
            painel["painel<br/>3 telas + API JSON"]
        end
    end

    proc[/"/proc do host"/]
    sock[/"/var/run/docker.sock"/]

    proc --> t1
    sock --> t2
    t3 -. "GET /" .-> api
    t3 -. "GET /" .-> rec

    t1 == "amostra" ==> buffer
    t2 == "amostra" ==> buffer
    t3 == "amostra" ==> buffer
    buffer ==> consumidor
    consumidor --> estado
    estado --> painel
    painel --> navegador
```

| Nível | Fonte | O que vai para o painel |
|---|---|---|
| 1 — hardware/SO | `/proc/stat`, `/proc/meminfo`, `/proc/<pid>/stat`, `statvfs` | CPU total e por núcleo, memória, disco, processos (os 25 maiores por CPU) e total de threads |
| 2 — containers | API da Docker Engine pelo socket, somente leitura | status, saúde, CPU, memória usada e limite, reinícios e `OOMKilled` |
| 3 — serviços | `GET /` em cada serviço, timeout de 2 s | estado, código HTTP, tempo de resposta e há quanto tempo está nesse estado |

**Por que três threads e não um laço só.** Ler `/proc` leva ~2 ms, a API do
Docker leva centenas de milissegundos (duas chamadas por container) e um health
check de serviço fora do ar leva o timeout inteiro. Num laço único, a curva de
CPU ganharia um buraco de 2 s toda vez que um serviço caísse, justamente quando
a medição mais importa.

**Por que threads servem aqui, apesar do GIL.** As três produtoras passam quase
todo o tempo esperando I/O (arquivo, socket, resposta HTTP), e o GIL é liberado
na espera. É o contrário do recálculo de recomendações, que é puro CPU e usa
processos.

**Por que um buffer no meio.** As estruturas do estado não são thread-safe, e as
regras de alerta comparam cada amostra com a situação anterior: com uma única
thread escrevendo, essa comparação acontece numa ordem só. O buffer também
absorve rajadas, porque com ele cheio a produtora espera em vez de acumular
amostras sem limite. É o mesmo `BufferLimitado` da demonstração de
produtor-consumidor, agora em uso real.

**Alertas** ([`monitor/eventos.py`](monitor/eventos.py), testáveis sem Docker e
sem `/proc`): memória de container acima de **80%** do limite, memória da máquina
acima de **90%**, `OOMKilled` (crítico), reinício e mudança de status de
container, queda e volta de serviço. Cada regra dispara **na mudança**, não a
cada amostra: sem isso, um container em 85% encheria a tela com o mesmo alerta a
cada 5 segundos.

**As três telas** (atualizam a cada 2 s; HTML, CSS e JS próprios, sem framework e
sem build):

| Tela | Caminho | Conteúdo |
|---|---|---|
| Hardware e SO | `/painel/` | cartões de CPU, memória, disco, processos e uptime; gráficos das últimas 90 amostras; CPU por núcleo; tabela de processos |
| Containers | `/painel/containers.html` | um container por linha, com barra de memória contra o limite, reinícios e `OOMKilled`; ordenável por CPU, memória ou nome |
| Serviços | `/painel/servicos.html` | estado e tempo de resposta de cada serviço, e a linha do tempo de eventos |

A faixa do topo mostra os alertas conforme chegam, e o cabeçalho mostra o estado
do próprio coletor: threads vivas, ocupação do buffer, amostras recebidas e
altura da árvore de eventos.

| Endpoint | Para quê |
|---|---|
| `GET /api/resumo` | threads, buffer e contadores das estruturas |
| `GET /api/nivel1?janela=90` | última amostra do SO e as séries dos gráficos |
| `GET /api/nivel2?ordenar_por=cpu\|memoria\|nome` | containers |
| `GET /api/nivel3?minutos=15` | serviços e linha do tempo |
| `GET /api/eventos?minutos=&severidade=` | consulta por intervalo na árvore |
| `GET /api/alertas` | retira os alertas da fila (cada um é entregue uma vez) |
| `GET /api/historico/nivel1?desde=<timestamp>` | corte do histórico por busca binária |

O serviço precisa de dois acessos especiais, declarados no
[`docker-compose.yml`](docker-compose.yml): o socket do Docker montado **somente
leitura** (sem ele, a tela de containers mostra o aviso e os outros dois níveis
continuam funcionando) e `pid: host` com `/:/hostfs:ro`, para o nível 1 enxergar
os processos e o disco da máquina em vez dos do próprio container.

**Evidência**, com os containers de pé:

```bash
python evidencia_monitor.py
```

Confere as 4 threads e os três níveis, derruba o `recomendacoes` com
`docker compose stop`, confirma o evento crítico na linha do tempo, religa e
confirma o evento de volta. Roda no CI a cada push, com os containers de
verdade, e o arquivo gerado fica anexado à execução
([`evidencias/pi3/README.md`](evidencias/pi3/README.md)).

**Monitor de processo — `scripts/monitor.sh` (Lab 09)**

```bash
./scripts/monitor.sh <PID|nome> [intervalo_s] [amostras] [limite_cpu]
./scripts/monitor.sh postgres          # padrão: 1 s, 30 amostras, alerta acima de 80%
./scripts/monitor.sh 1234 0.5 60 50
```

- Nome em vez de número: o PID vem do `pgrep -o`.
- Gera `medicoes_<nome>_<data>.csv` (`timestamp,pid,cpu,mem,rss_kb,threads,estado`) e acrescenta alertas em `alertas.log`.
- Para quando o processo termina; `Ctrl+C` também imprime o resumo (CPU média e máxima, RSS médio, máximo de threads).

</details>

## Declaração de uso de Inteligência Artificial (Critério 6)

Usamos o **Claude** (Anthropic) para revisar o endpoint de pedidos, projetar o
teste de concorrência, implementar a fila assíncrona (Redis/RQ), depurar o
ambiente e, na revisão final (via Claude Code), auditar o projeto contra a
rubrica, o que levou ao reconciliador, ao disjuntor, ao retry com status
`falhou`, à idempotência e aos testes de resiliência e controle negativo.

<details>
<summary><b>Ver as correções que fizemos nas sugestões da IA</b></summary>

<br>

- **Status 200 x 201:** a primeira versão do `test_concorrencia.py` contava
  sucessos com `status_code == 200`, mas o `POST /pedidos` retorna **201**, então
  todo pedido bem-sucedido virava "erro inesperado". Comparamos com o `main.py` e corrigimos.
- **psycopg2 no Windows:** conectar direto ao PostgreSQL pelo Windows esbarrou
  num bug de locale (erro de decodificação em português). Passamos a verificar
  o banco via `docker compose exec postgres psql`.
- **Campo `status` esquecido:** a IA criou o `PedidoStatusResponse` mas não
  adicionou `status` ao `PedidoResponse`; o Pydantic descartava o campo em
  silêncio. Também aumentamos o timeout do `test_fila.py` de 15 s para 30 s e
  escalamos para 3 workers.
- **Redis fora derrubava a compra:** na auditoria, com o Redis desligado, o
  `POST /pedidos` devolvia **500** após ~7 s com a compra já gravada. A primeira
  proposta da IA (só timeouts curtos) não resolvia: a demora vinha da resolução
  DNS do host `redis` (~4 s), que ignora esses timeouts. Por isso entraram o
  disjuntor e a publicação depois da resposta. A IA também verificou no código
  do RQ 1.16 que o worker precisava de uma conexão própria (o timeout curto
  quebraria a espera bloqueante por jobs).
- **Árvore de eventos degenerando (monitor):** a chave dos eventos é o horário, e
  horários chegam em ordem crescente, o que transforma uma BST sem balanceamento
  numa lista (altura = n) e faz a consulta por intervalo voltar a custar O(n),
  exatamente o que a árvore deveria evitar. A primeira versão não tratava isso.
  Entraram o rebalanceamento por reconstrução, um teste que falha se a altura
  passar de ~2·log₂(n) e a altura exposta no painel, para dar para ver o efeito
  rodando.
- **Serviço que já nascia fora do ar não gerava evento (monitor):** a regra só
  olhava transições e não havia estado anterior para comparar, então o painel
  mostrava "fora" sem nada na linha do tempo explicando desde quando. A regra
  passou a tratar a primeira observação como evento quando o serviço não está no ar.
- **Barras de memória invisíveis (painel):** a IA usou `span` com `height`, que um
  elemento inline ignora; corrigido à mão com `display: block`.

</details>
