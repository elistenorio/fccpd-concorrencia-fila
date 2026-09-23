# Evidências de teste — Origem

Gerado automaticamente por `python gerar_evidencias.py` em 22/09/2026 23:28:20 (UTC-0300) (duração: 1min13s).  
Commit testado: `21b7454` (detalhes em [ambiente.txt](ambiente.txt)).

**Resultado geral: ✅ 7 de 7 execuções aprovadas.**

## 1. Concorrência (Critérios 1 e 2) — 5 rodadas

Cada rodada dispara 50 compras simultâneas de um produto com 10 unidades, primeiro contra uma cópia da API **sem** o lock (controle negativo) e depois contra a API real.

| Rodada | Sem lock: pedidos aceitos | Com lock: 201 | 409 | Erros | Estoque final | Pedidos no banco | Resultado |
|---|---|---|---|---|---|---|---|
| [1](01_concorrencia_rodada_1.txt) | 50 de 10 unidades | 10 | 40 | 0 | 0 | 10 | ✅ |
| [2](01_concorrencia_rodada_2.txt) | 45 de 10 unidades | 10 | 40 | 0 | 0 | 10 | ✅ |
| [3](01_concorrencia_rodada_3.txt) | 44 de 10 unidades | 10 | 40 | 0 | 0 | 10 | ✅ |
| [4](01_concorrencia_rodada_4.txt) | 50 de 10 unidades | 10 | 40 | 0 | 0 | 10 | ✅ |
| [5](01_concorrencia_rodada_5.txt) | 50 de 10 unidades | 10 | 40 | 0 | 0 | 10 | ✅ |

Com o lock: 250 compras simultâneas no total, 50 vendas, 200 recusas por falta de estoque e 0 erros. Sem o lock, a mesma carga vendeu 239 unidades de um estoque total de 50.

## 2. Fila assíncrona (Critério 3)

| Latência do POST (job leva 3000 ms) | 5 pedidos processados em | Resultado |
|---|---|---|
| mín 16 ms · mediana 16 ms · máx 22 ms | 13.7 s | [✅](02_fila.txt) |

## 3. Resiliência com o Redis fora do ar (Critério 4)

| Compras com 201 | Latência do POST | Estoque | Processados após religar | Resultado |
|---|---|---|---|---|
| 5 de 5 | mediana 52 ms · máx 70 ms | 10 -> 5 | 5 de 5 em 18.3s | [✅](03_resiliencia.txt) |

A [linha do tempo](03_resiliencia_logs.txt) mostra, pelos logs dos próprios containers (29 eventos), o Redis caindo, as compras sendo respondidas com 201 mesmo assim, os workers perdendo a conexão, o Redis voltando, o reconciliador republicando os pedidos órfãos e os workers processando cada um deles.

## Arquivos

- [ambiente.txt](ambiente.txt): data, commit, versões e containers
- [01_concorrencia_rodada_1.txt](01_concorrencia_rodada_1.txt)
- [01_concorrencia_rodada_2.txt](01_concorrencia_rodada_2.txt)
- [01_concorrencia_rodada_3.txt](01_concorrencia_rodada_3.txt)
- [01_concorrencia_rodada_4.txt](01_concorrencia_rodada_4.txt)
- [01_concorrencia_rodada_5.txt](01_concorrencia_rodada_5.txt)
- [02_fila.txt](02_fila.txt)
- [03_resiliencia.txt](03_resiliencia.txt)
- [03_resiliencia_logs.txt](03_resiliencia_logs.txt)
