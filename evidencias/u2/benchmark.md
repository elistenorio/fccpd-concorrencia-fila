# Benchmark do cálculo de recomendações

Gerado por `python -m recomendacoes.benchmark` (ver docstring do script para a metodologia completa).

## Ambiente
| Item | Valor |
|---|---|
| Data | 05/11/2026 21:28:59 (UTC+0000) |
| CPU | Intel(R) Core(TM) i5-1035G1 CPU @ 1.00GHz |
| Núcleos visíveis no container | 8 |
| Memória visível no container | 3.7 GB |
| Python | 3.12.16 |
| Sistema (container) | Linux 6.6.87.2-microsoft-standard-WSL2 |

## Metodologia
- **Medido:** tempo de `recomendacoes.calculo.calcular()` (o trecho paralelo), incluindo a criação do pool de threads/processos. Leitura do banco e gravação ficam de fora (iguais nos três modos).
- **Dados:** `app.seed.gerar_dados` com semente 42; clientes = 5·n, pedidos = 40·n; n² − n comparações de pares.
- **Repetições:** 1 aquecimento descartado + 5 medições por configuração; reporta-se a mediana, mínimo, máximo e desvio padrão.
- **Correção:** o resultado de threads e processos foi comparado com o sequencial em todas as configurações: idêntico em todas.
- **Speedup** = tempo sequencial / tempo do modo; **eficiência** = speedup / workers.

## Experimento A — efeito do volume (4 workers)
| Produtos | Comparações | Modo | Workers | Tempo mediano (s) | Mín–máx (s) | Desvio (s) | Vazão (comparações/s) | Speedup | Eficiência |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 250 | 62.250 | sequencial | 1 | 0.046 | 0.043–0.048 | 0.002 | 1.362.386 | 1.00× | 1.00 |
| 250 | 62.250 | threads | 4 | 0.060 | 0.051–0.068 | 0.006 | 1.041.023 | 0.76× | 0.19 |
| 250 | 62.250 | processos | 4 | 0.210 | 0.209–0.233 | 0.010 | 296.326 | 0.22× | 0.05 |
| 500 | 249.500 | sequencial | 1 | 0.285 | 0.198–0.431 | 0.103 | 876.079 | 1.00× | 1.00 |
| 500 | 249.500 | threads | 4 | 0.276 | 0.242–0.307 | 0.026 | 902.523 | 1.03× | 0.26 |
| 500 | 249.500 | processos | 4 | 0.405 | 0.286–0.580 | 0.110 | 616.315 | 0.70× | 0.18 |
| 1000 | 999.000 | sequencial | 1 | 1.165 | 0.924–1.883 | 0.434 | 857.562 | 1.00× | 1.00 |
| 1000 | 999.000 | threads | 4 | 1.137 | 0.948–1.305 | 0.134 | 878.275 | 1.02× | 0.26 |
| 1000 | 999.000 | processos | 4 | 1.147 | 0.884–1.202 | 0.133 | 871.244 | 1.02× | 0.25 |
| 2000 | 3.998.000 | sequencial | 1 | 3.699 | 3.451–4.152 | 0.280 | 1.080.719 | 1.00× | 1.00 |
| 2000 | 3.998.000 | threads | 4 | 4.088 | 3.894–4.257 | 0.139 | 977.936 | 0.90× | 0.23 |
| 2000 | 3.998.000 | processos | 4 | 1.607 | 1.552–1.739 | 0.076 | 2.488.388 | 2.30× | 0.58 |
| 4000 | 15.996.000 | sequencial | 1 | 14.014 | 13.198–15.493 | 0.950 | 1.141.394 | 1.00× | 1.00 |
| 4000 | 15.996.000 | threads | 4 | 13.780 | 13.647–14.508 | 0.415 | 1.160.826 | 1.02× | 0.25 |
| 4000 | 15.996.000 | processos | 4 | 4.821 | 4.142–7.671 | 1.434 | 3.318.284 | 2.91× | 0.73 |

## Experimento B — efeito do nº de workers (2000 produtos)
| Produtos | Comparações | Modo | Workers | Tempo mediano (s) | Mín–máx (s) | Desvio (s) | Vazão (comparações/s) | Speedup | Eficiência |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 2000 | 3.998.000 | sequencial | 1 | 4.102 | 3.693–4.592 | 0.328 | 974.692 | 1.00× | 1.00 |
| 2000 | 3.998.000 | threads | 2 | 4.145 | 3.882–4.474 | 0.213 | 964.485 | 0.99× | 0.49 |
| 2000 | 3.998.000 | threads | 4 | 3.714 | 3.677–4.146 | 0.194 | 1.076.479 | 1.10× | 0.28 |
| 2000 | 3.998.000 | threads | 8 | 3.762 | 3.702–3.810 | 0.043 | 1.062.815 | 1.09× | 0.14 |
| 2000 | 3.998.000 | processos | 1 | 3.378 | 3.325–3.462 | 0.050 | 1.183.529 | 1.21× | 1.21 |
| 2000 | 3.998.000 | processos | 2 | 2.112 | 2.057–2.499 | 0.190 | 1.893.327 | 1.94× | 0.97 |
| 2000 | 3.998.000 | processos | 4 | 1.540 | 1.458–1.639 | 0.066 | 2.596.190 | 2.66× | 0.67 |
| 2000 | 3.998.000 | processos | 6 | 1.771 | 1.728–2.574 | 0.365 | 2.257.395 | 2.32× | 0.39 |
| 2000 | 3.998.000 | processos | 8 | 2.403 | 1.911–3.011 | 0.421 | 1.663.858 | 1.71× | 0.21 |

## Diagnóstico — por que o ganho não é linear

### C — teto do hardware (laço puro de CPU, 16 tarefas iguais)
| Workers | Tempo (s) | Speedup |
|---:|---:|---:|
| 1 (sequencial) | 3.318 | 1.00× |
| 1 (processos) | 4.097 | 0.81× |
| 2 (processos) | 2.970 | 1.12× |
| 4 (processos) | 1.438 | 2.31× |
| 6 (processos) | 1.239 | 2.68× |
| 8 (processos) | 1.416 | 2.34× |

### D — custo fixo de criar e encerrar o pool de processos (8 produtos, cálculo desprezível)
| Workers | Tempo para criar e encerrar o pool (s) |
|---:|---:|
| 1 | 0.170 |
| 2 | 0.169 |
| 4 | 0.243 |
| 6 | 0.323 |
| 8 | 0.536 |

### E — contenção: o mesmo trabalho fica mais lento quando há mais workers ao mesmo tempo
| Workers simultâneos | Tempo de um bloco de 100 produtos (s) | Em relação a 1 worker |
|---:|---:|---:|
| 1 | 0.183 | 1.00× |
| 2 | 0.199 | 1.09× |
| 4 | 0.338 | 1.85× |
| 6 | 0.375 | 2.05× |
| 8 | 0.432 | 2.36× |
