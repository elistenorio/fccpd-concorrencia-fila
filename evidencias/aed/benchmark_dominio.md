# Benchmark do modelo de domínio (AED)

Gerado por `python -m dominio.benchmark` em 2026-11-26 23:22 (16 s). Python 3.12.16, Linux x86_64, os.cpu_count() = 8. Mediana de 5 rodadas; dados sintéticos com semente fixa (42 + n).

| n | Teste | Estrutura | Tempo por operação | Alternativa | Tempo por operação | Razão |
|---:|---|---|---:|---|---:|---:|
| 1.000 | A consulta por código | TabelaHash | 4.37 µs | varredura da lista | 38.07 µs | 9× |
| 1.000 | B busca por prefixo | ArvoreBusca (altura 22) | 9.28 µs | varredura + filtro | 199.54 µs | 22× |
| 1.000 | C faixa de preço (±500) | busca binária | 7.89 µs | varredura + filtro | 110.94 µs | 14× |
| 1.000 | C' refazer catálogo | merge_sort ×2 | 9.62 ms | — | — | — |
| 1.000 | D 1000 lotes por validade | chegando em ordem | 3.67 µs | ordem aleatória | 27.39 µs | 7× |
| 1.000 | E 1000 pedidos (entrar + sair) | Fila FIFO | 4.01 µs | ListaOrdenada (prioridade) | 23.91 µs | 6× |
| 10.000 | A consulta por código | TabelaHash | 2.18 µs | varredura da lista | 536.40 µs | 247× |
| 10.000 | B busca por prefixo | ArvoreBusca (altura 31) | 54.34 µs | varredura + filtro | 1.86 ms | 34× |
| 10.000 | C faixa de preço (±500) | busca binária | 17.88 µs | varredura + filtro | 1.22 ms | 68× |
| 10.000 | C' refazer catálogo | merge_sort ×2 | 119.08 ms | — | — | — |
| 10.000 | D 2000 lotes por validade | chegando em ordem | 2.34 µs | ordem aleatória | 34.19 µs | 15× |
| 10.000 | E 5000 pedidos (entrar + sair) | Fila FIFO | 3.26 µs | ListaOrdenada (prioridade) | 117.08 µs | 36× |
| 50.000 | A consulta por código | TabelaHash | 1.96 µs | varredura da lista | 2.78 ms | 1.420× |
| 50.000 | B busca por prefixo | ArvoreBusca (altura 37) | 315.73 µs | varredura + filtro | 12.51 ms | 40× |
| 50.000 | C faixa de preço (±500) | busca binária | 39.52 µs | varredura + filtro | 3.69 ms | 93× |
| 50.000 | C' refazer catálogo | merge_sort ×2 | 793.90 ms | — | — | — |
| 50.000 | D 2000 lotes por validade | chegando em ordem | 3.16 µs | ordem aleatória | 37.98 µs | 12× |
| 50.000 | E 5000 pedidos (entrar + sair) | Fila FIFO | 3.02 µs | ListaOrdenada (prioridade) | 102.14 µs | 34× |
