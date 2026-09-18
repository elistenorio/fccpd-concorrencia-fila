# Origem

Backend do marketplace de artesanato Origem, desenvolvido para a disciplina FCCPD. Este repositório cobre o fluxo de compra com controle de concorrência e consistência de estoque (critérios 1 e 2 da rubrica) e a fila assíncrona de processamento de pedidos (critérios 3 e 4).

## Equipe

- Aguinaldo Neto (@netokemon)
- Caliel Feijó (@poeisie)
- Elis Tenório (@elistenorio)
- Eulália Albuquerque (@eulalialbuquerque)
- Giulia Ferreira (@giumari18)
- Sarah Cyrne (@sarahcyrne)

## Stack

- **API**: Python + FastAPI + SQLAlchemy
- **Banco**: PostgreSQL 16
- **Fila/broker**: Redis + RQ
- **Orquestração local**: Docker Compose

## Como rodar

```bash
docker compose up -d --build --scale worker=3
```

Isso sobe 6 serviços: `postgres`, `redis`, `api` e 3 réplicas de `worker`. A API
fica disponível em `http://localhost:8000` (`GET /` retorna `{"status": "ok"}`
quando tudo está de pé). Rodar múltiplos workers em paralelo permite que a fila
processe vários pedidos ao mesmo tempo, em vez de um por vez.

## Endpoint principal

`POST /pedidos` — cria um pedido de compra para um produto.

- Recebe `{"produto_id": <int>}`
- Retorna **201** com os dados do pedido em caso de sucesso
- Retorna **409** se o produto estiver sem estoque
- Retorna **404** se o produto não existir

`GET /pedidos/{pedido_id}` — consulta o status de um pedido (`"pendente"` ou
`"processado"`).

## Critério 1 — Controle de concorrência

O `produto` é bloqueado a nível de linha antes da leitura/atualização do estoque,
usando lock pessimista do PostgreSQL (`SELECT ... FOR UPDATE` via
`with_for_update()` do SQLAlchemy). Enquanto uma transação segura o lock, qualquer
outra transação concorrente que tente ler a mesma linha fica bloqueada até o
`COMMIT` (ou `ROLLBACK`) da primeira.

**Por que lock pessimista, e não lock otimista:** o Origem vende peças únicas de
artesanato, então a disputa pelo estoque acontece de forma pontual — no exato
momento em que duas pessoas clicam em "comprar" ao mesmo tempo. Lock otimista
exigiria implementar lógica de retry no cliente (detectar conflito de versão,
tentar de novo), o que complicaria o MVP sem trazer ganho real para esse cenário
de disputa curta e pouco frequente. Lock pessimista resolve com uma linha de
código e garante correção imediata.

## Critério 2 — Consistência de estoque sob concorrência

A prova de que o estoque se mantém correto sob concorrência está no
[teste de concorrência](#como-rodar-o-teste-de-concorrência) abaixo, que dispara
50 requisições simultâneas contra um produto com 10 unidades em estoque e confere
o resultado tanto pelas respostas HTTP quanto pelo estado real do PostgreSQL.

## Critério 3 — Publicação de eventos (produtor)

Ao criar um pedido, o `POST /pedidos` não faz o processamento pós-venda (ex.:
emissão de nota fiscal, notificação ao vendedor) de forma síncrona. Em vez
disso, o pedido nasce com `status="pendente"` e a API publica um evento na fila
`pedidos` do Redis/RQ (`fila_pedidos.enqueue(processar_pedido, pedido.id)`),
retornando a resposta ao cliente imediatamente, sem esperar esse trabalho
terminar.

## Critério 4 — Processamento assíncrono (consumidor)

O serviço `worker` (`app/worker.py`) roda um `Worker` do RQ escutando a fila
`pedidos`, em um processo completamente separado da API. Quando consome um
evento, ele executa `processar_pedido` (`app/tasks.py`), que simula o trabalho
pesado e atualiza o pedido para `status="processado"`. Rodamos 3 réplicas desse
worker em paralelo (`--scale worker=3`), então vários pedidos são processados
simultaneamente em vez de um por vez — a fila escala horizontalmente só
adicionando mais réplicas, sem mudar nada no código.

## Como rodar o teste de concorrência

Com os containers de pé (`docker compose up -d`), no host (fora do container):

```bash
pip install httpx
python test_concorrencia.py
```

O script:
1. Reseta o produto de teste (`id=1`) com estoque conhecido (10 unidades);
2. Dispara 50 requisições `POST /pedidos` simultâneas contra ele;
3. Confere via `docker compose exec postgres psql` o estoque final e o número de
   pedidos criados no banco;
4. Falha (exit code ≠ 0) se qualquer número não bater com o esperado.

### Relatório de teste (evidência — Critério 5)

```
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

De 50 tentativas simultâneas para um produto com 10 unidades, exatamente 10
pedidos foram criados e 40 foram rejeitados por falta de estoque, com o estoque
final zerado — nenhuma venda duplicada, nenhum estoque negativo.

## Como rodar o teste da fila

Com os containers de pé, incluindo os 3 workers (`docker compose up -d --scale worker=3`):

```bash
docker compose cp test_fila.py api:/code/test_fila.py
docker compose exec api python test_fila.py
```

O script:
1. Reseta o produto de teste (`id=1`) com estoque conhecido;
2. Dispara 5 requisições `POST /pedidos`;
3. Confirma que os pedidos ficam `"pendente"` logo após a criação (prova de que
   o processamento não é síncrono);
4. Faz polling no PostgreSQL até todos os pedidos virarem `"processado"` ou
   estourar um timeout de 15s;
5. Falha se algum pedido não for processado dentro do prazo.

### Relatório de teste (evidência — Critérios 3 e 4)

[OK] 5 pedidos criados, todos processados pelo worker em até 15s.

Os 5 pedidos foram criados com `status="pendente"` e, com 3 workers RQ
consumindo a fila em paralelo, todos foram processados e migraram para
`status="processado"` dentro do tempo esperado — confirmando que o
processamento acontece de fato fora do ciclo da requisição HTTP.

## Declaração de uso de Inteligência Artificial (Critério 6)

Usamos o **Claude** (Anthropic) para revisar o código do endpoint de pedidos,
projetar o script de teste de concorrência, implementar a fila assíncrona de
processamento (produtor/consumidor com Redis/RQ) e depurar problemas de
ambiente durante o desenvolvimento.

Um exemplo de sugestão da IA que precisou ser corrigida: a primeira versão do
`test_concorrencia.py` verificava `status_code == 200` para contar sucessos, mas
o endpoint `POST /pedidos` retorna **201 Created**. Isso fazia o teste registrar
falsamente todos os pedidos bem-sucedidos como "erros inesperados". Percebemos a
inconsistência ao comparar o código do `main.py` com a lógica do teste, e
corrigimos a verificação para `status_code == 201`.

Outro ajuste feito durante a depuração: a primeira abordagem do teste conectava
diretamente ao PostgreSQL via SQLAlchemy/psycopg2 a partir do Windows, mas isso
esbarrou em um bug de locale do psycopg2 no Windows (erro de decodificação ao
tentar reportar falhas de conexão em português). Em vez de insistir nessa rota,
optamos por rodar as verificações de estado do banco via `docker compose exec
postgres psql`, o que eliminou o problema por completo.

Na implementação da fila, a IA também errou de início: ao adicionar o campo
`status` na resposta do `POST /pedidos`, a