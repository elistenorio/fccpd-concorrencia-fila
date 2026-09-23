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

Isso sobe 7 containers: `postgres`, `redis`, `api`, 3 réplicas de `worker` e o
`reconciliador`. A API fica disponível em `http://localhost:8000` (`GET /`
retorna `{"status": "ok"}` quando tudo está de pé). Rodar múltiplos workers em
paralelo permite que a fila processe vários pedidos ao mesmo tempo, em vez de
um por vez.

## Endpoint principal

`POST /pedidos` — cria um pedido de compra para um produto.

- Recebe `{"produto_id": <int>}`
- Retorna **201** com os dados do pedido em caso de sucesso
- Retorna **409** se o produto estiver sem estoque
- Retorna **404** se o produto não existir

`GET /pedidos/{pedido_id}` — consulta o status de um pedido (`"pendente"`,
`"processado"` ou `"falhou"`).

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
emissão de nota fiscal, notificação ao vendedor) de forma síncrona. O pedido
nasce com `status="pendente"`, a API responde **201** imediatamente e só depois
de enviar a resposta publica um job na fila `pedidos` do Redis/RQ
(`publicar_pedido` em `app/queue.py`). Cada job:

- tem um id fixo por pedido (`pedido-<id>`), o que permite saber se um pedido
  já está na fila sem publicá-lo em duplicidade;
- referencia a tarefa pelo caminho (`"app.tasks.processar_pedido"`), então a
  API não precisa importar o código do worker;
- é reexecutado automaticamente se falhar (`Retry(max=3, interval=[5, 15, 30])`).

## Critério 4 — Desacoplamento e integridade das mensagens (consumidor)

O serviço `worker` (`app/worker.py`) roda um `Worker` do RQ escutando a fila
`pedidos`, em um processo completamente separado da API. Quando consome um
evento, ele executa `processar_pedido` (`app/tasks.py`), que simula o trabalho
pesado e atualiza o pedido para `status="processado"`. Rodamos 3 réplicas desse
worker em paralelo (`--scale worker=3`), então vários pedidos são processados
simultaneamente em vez de um por vez — a fila escala horizontalmente só
adicionando mais réplicas, sem mudar nada no código.

**A fila não segura a loja.** A compra é efetivada no PostgreSQL (estoque
baixado e pedido gravado na mesma transação) antes de qualquer coisa ir para a
fila, e a publicação só acontece depois que a resposta foi enviada. Por isso,
nem um Redis lento nem um Redis fora do ar atrasam ou derrubam uma compra.

**Nenhum pedido se perde.** O banco é a fonte da verdade, e cada falha possível
tem um tratamento:

| O que falha | O que acontece |
|---|---|
| Redis fora na hora da compra | A compra responde 201 normalmente e o pedido fica `pendente`. O **reconciliador** (`app/reconciliador.py`, container próprio) procura a cada 5 s pedidos pendentes há mais de 10 s sem job no Redis e os publica quando a fila volta. Um disjuntor na API para de tentar o Redis por 15 s depois de uma falha, para não prender threads da loja tentando conectar. |
| Redis reinicia com jobs na fila | O Redis roda com `--appendonly yes`: a fila é gravada em disco e sobrevive ao reinício. O que se perder no último segundo antes de uma queda é coberto pelo reconciliador. |
| Job falha (ex.: banco fora durante o processamento) | O RQ tenta de novo até 3 vezes, esperando 5 s, 15 s e 30 s. Se todas falharem, o callback `marcar_pedido_como_falho` marca o pedido como `falhou` (visível no `GET /pedidos/{id}`), em vez de ele ficar `pendente` para sempre. |
| Worker morre no meio de um job | Na limpeza periódica dos registros, o RQ detecta o job abandonado e aplica o mesmo retry. |
| O mesmo pedido chega duas vezes (retry, reconciliador) | A tarefa é idempotente: só trabalha em pedidos ainda `pendente` e marca `processado` com um UPDATE condicional, então a nota fiscal não é emitida duas vezes. |

A prova de que isso funciona está no [teste de resiliência](#como-rodar-o-teste-de-resiliência-redis-fora-do-ar).

## Como rodar o teste de concorrência

Com os containers de pé (`docker compose up -d`), no host (fora do container):

```bash
pip install httpx
python test_concorrencia.py --controle-negativo
```

O script:
1. Reseta o produto de teste (`id=1`) com estoque conhecido (10 unidades);
2. Dispara 50 requisições `POST /pedidos` simultâneas contra ele;
3. Confere via `docker compose exec postgres psql` o estoque final e o número de
   pedidos criados no banco;
4. Falha (exit code ≠ 0) se qualquer número não bater com o esperado.

Com `--controle-negativo`, antes do teste real ele repete o mesmo ataque contra
uma segunda instância da API (container temporário na porta 8001) em que o
`with_for_update()` foi desligado só dentro dela, sem mudar o código do
projeto. Sem essa flag, roda só o teste real.

### Relatório de teste (evidência — Critério 5)

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

De 50 tentativas simultâneas para um produto com 10 unidades, exatamente 10
pedidos foram criados e 40 foram rejeitados por falta de estoque, com o estoque
final zerado — nenhuma venda duplicada, nenhum estoque negativo.

O controle negativo mostra por que isso importa: com o mesmo ataque e sem o
lock, 49 compras foram aceitas para um estoque de 10 (39 peças únicas vendidas
a mais) e 39 baixas de estoque se perderam. Isso prova que o teste consegue
detectar a condição de corrida, então o resultado perfeito da API real vem do
lock, e não de sorte.

## Como rodar o teste da fila

Com os containers de pé, incluindo os 3 workers (`docker compose up -d --scale worker=3`):

```bash
docker compose cp test_fila.py api:/code/test_fila.py
docker compose exec api python test_fila.py
```

O script:
1. Reseta o produto de teste (`id=1`) com estoque conhecido;
2. Dispara 5 requisições `POST /pedidos`;
3. Confirma que cada compra responde 201 já com `status="pendente"` e em menos
   de 500 ms, enquanto cada job leva 3 s (prova de que a compra não espera o
   processamento);
4. Faz polling no PostgreSQL até todos os pedidos virarem `"processado"` ou
   estourar um timeout de 30s;
5. Falha se alguma dessas condições não for atendida.

### Relatório de teste (evidência — Critérios 3 e 4)

```
=== RELATÓRIO DE TESTE DA FILA ===
Pedidos criados: 5 (todos responderam 201 com status 'pendente')
Latência do POST /pedidos: mín 29 ms | mediana 43 ms | máx 54 ms (cada job leva 3000 ms)
Todos processados pelos workers em 10.1s (limite: 30s)

[OK] 5 pedidos criados, todos processados pelo worker em até 30s, sem a compra esperar pelo processamento.
```

Os 5 pedidos foram criados com `status="pendente"` em no máximo 54 ms, cerca de
55 vezes mais rápido que o trabalho de 3 s de cada job. Com 3 workers RQ
consumindo a fila em paralelo, todos migraram para `status="processado"` dentro
do prazo, confirmando que o processamento acontece de fato fora do ciclo da
requisição HTTP.

## Como rodar o teste de resiliência (Redis fora do ar)

Com os containers de pé, no host (fora do container), assim como o teste de
concorrência:

```bash
python test_resiliencia.py
```

O script:
1. Reseta o produto de teste (`id=1`) com estoque conhecido (10 unidades);
2. Derruba o Redis (`docker compose stop redis`) e faz 5 compras;
3. Confirma que todas responderam 201 em menos de 1 s, que os 5 pedidos estão
   `"pendente"` no banco e que o estoque baixou exatamente 5 unidades;
4. Religa o Redis (sempre, mesmo se o teste falhar) e espera o reconciliador e
   os workers levarem todos os pedidos a `"processado"`;
5. Falha se alguma dessas condições não for atendida.

### Relatório de teste (evidência — Critério 4)

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

Antes dessas mudanças, o mesmo cenário devolvia **HTTP 500 depois de ~7 s**,
embora a compra já tivesse sido gravada: o estoque baixava, o pedido ficava
`pendente` para sempre e o cliente, vendo o erro, tentaria comprar de novo.

## Evidências completas (Critério 5)

Os relatórios acima são de uma única execução de cada teste. A pasta
[`evidencias/`](evidencias/RESUMO.md) reúne uma bateria completa, gerada por um
script a partir dos próprios testes (nada é copiado à mão):

- 5 rodadas do teste de concorrência, cada uma com o controle negativo;
- o teste da fila e o teste de resiliência;
- a linha do tempo da queda do Redis, extraída dos logs dos containers, que
  mostra as compras respondidas com 201, o reconciliador republicando os
  pedidos órfãos e os workers processando cada um;
- o ambiente: data, commit testado, versões e containers.

Com os containers de pé, no host:

```bash
python gerar_evidencias.py
```

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
`status` na resposta do `POST /pedidos`, a sugestão inicial só criou o novo
schema `PedidoStatusResponse` (para o `GET /pedidos/{id}`) mas esqueceu de
adicionar o campo `status` no `PedidoResponse` já existente. O código rodava
sem erro, só que a resposta da criação do pedido vinha sem o campo — o Pydantic
descarta silenciosamente valores que não estão declarados no schema. Também
identificamos, ao rodar o `test_fila.py` pela primeira vez, que o timeout
inicial de 15s era curto demais para 5 pedidos processados sequencialmente por
um único worker (cada um simula 3s de trabalho); além de aumentar o timeout
para 30s, escalamos para 3 workers em paralelo (`--scale worker=3`), o que
também deixou mais evidente a capacidade da fila de escalar horizontalmente.

Na revisão final, usamos o Claude (via Claude Code) para auditar o projeto
contra a rubrica. Rodando os testes e derrubando o Redis de propósito, ele
encontrou um acoplamento que nossos testes não cobriam: com o Redis fora, o
`POST /pedidos` devolvia **500** depois de ~7 s, embora a compra já estivesse
gravada, o que deixava o pedido `pendente` para sempre e levaria o cliente a
comprar de novo. A partir disso, a IA implementou a publicação na fila depois
da resposta, o disjuntor, o reconciliador, o retry com callback de falha
(status `falhou`) e a idempotência da tarefa, além do controle negativo no
`test_concorrencia.py`, do `test_resiliencia.py` e do `gerar_evidencias.py`.

A primeira proposta da IA para esse problema também estava incompleta: ela
sugeriu apenas configurar timeouts curtos na conexão com o Redis. Ao medir,
vimos que a demora vinha da resolução DNS do host `redis` (~4 s com o
container parado), que não respeita esses timeouts. Por isso foram adicionados
o disjuntor e a publicação depois da resposta. A IA também verificou no
código-fonte do RQ 1.16 que o worker não podia usar a mesma conexão com timeout
curto (isso quebraria a espera bloqueante por jobs), e por isso o worker tem
uma conexão própria.
