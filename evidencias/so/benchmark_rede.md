# Benchmark de rede (latência, vazão, perda, jitter)

Gerado por `python -m rede.benchmark` em 2026-11-27 00:10: cliente assíncrono (httpx) dentro da rede do Docker Compose, 20 s por rodada, 3 repetições, pensar ~ exp(média 0.2 s), timeout 3 s. Python 3.12.16, os.cpu_count() = 8 (o cliente divide a máquina com os serviços).

Mediana das repetições e, entre parênteses, mínimo–máximo.

| Rede | Cenário | Usuários | Vazão (resp/s) | Vazão (kB/s) | p50 (ms) | p95 (ms) | p99 (ms) | Jitter (ms) | Perda (%) | Sem recomendações (%) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| antes (pool 5+10, conexão presa na chamada REST) | baixo | 5 | 22.5 (22.1–23.4) | 18.9 | 16.3 | 36.9 (32.5–39.1) | 53 | 9.9 (8.7–10) | 0 (0–0) | 0 |
| antes (pool 5+10, conexão presa na chamada REST) | moderado | 25 | 93.8 (91–102.4) | 79.2 | 32.6 | 135.4 (93.7–227.5) | 301.8 | 41.7 (28.3–52.6) | 0 (0–0) | 0 |
| antes (pool 5+10, conexão presa na chamada REST) | alto | 100 | 0.7 (0.6–0.8) | 1.4 | 668.95 | 763.85 (416.6–1111.1) | 763.85 | 0 (0–0) | 97.62 (97.39–97.85) | 0 |
| depois (pool 20+10, conexão devolvida antes da chamada REST) | baixo | 5 | 22.2 (21.5–23.4) | 18.5 | 18.2 | 39.7 (38.5–40.5) | 132.2 | 11.9 (10.9–14.3) | 0 (0–0) | 0 |
| depois (pool 20+10, conexão devolvida antes da chamada REST) | moderado | 25 | 88.1 (81.7–90.7) | 73.8 | 57.3 | 213.2 (167.7–349) | 485.3 | 54.3 (46.2–76.4) | 0 (0–0) | 0 |
| depois (pool 20+10, conexão devolvida antes da chamada REST) | alto | 100 | 60.2 (60–60.9) | 50.1 | 915.4 | 3963.6 (3627.7–3991.6) | 5295.6 | 1232.1 (1140–1236.8) | 0 (0–0.08) | 0 |
| degradada: netem no recomendacoes (200 ± 100 ms, 10% perda) | moderado | 25 | 94.9 (82.2–96.4) | 58.9 | 23 | 190.1 (132.2–266.4) | 1022.1 | 65 (51.7–106.6) | 0 (0–0) | 100 |

p95 por ação (mediana das repetições, ms):

| Rede | Cenário | Página do produto | Busca | Compra |
|---|---|---:|---:|---:|
| antes (pool 5+10, conexão presa na chamada REST) | baixo | 39.4 | 11.4 | 35.6 |
| antes (pool 5+10, conexão presa na chamada REST) | moderado | 141.9 | 39.4 | 140.3 |
| antes (pool 5+10, conexão presa na chamada REST) | alto | 0 | 763.85 | 0 |
| depois (pool 20+10, conexão devolvida antes da chamada REST) | baixo | 41.5 | 15.7 | 37.3 |
| depois (pool 20+10, conexão devolvida antes da chamada REST) | moderado | 227 | 69.3 | 232.4 |
| depois (pool 20+10, conexão devolvida antes da chamada REST) | alto | 4037.3 | 3466.2 | 3687.2 |
| degradada: netem no recomendacoes (200 ± 100 ms, 10% perda) | moderado | 202.2 | 89.3 | 241.6 |
