# Evidências do PI3 — monitor e carga

`monitor.txt` é gerado por [`evidencia_monitor.py`](../../evidencia_monitor.py),
que roda com os containers de pé e verifica, com dados reais, o coletor
concorrente (4 threads e o buffer entre elas), os três níveis de coleta, a
queda e a volta de um serviço na linha do tempo de eventos e as estruturas
próprias em uso.

```bash
pip install -r requirements-dev.txt   # os scripts rodam no host e usam httpx
docker compose up -d --build --wait
python evidencia_monitor.py
```

`carga.txt` é gerado por [`teste_carga.py`](../../teste_carga.py), que sustenta
uma taxa fixa de pedidos por alguns minutos e mede latência, vazão, o acúmulo
da fila e o que o painel registrou durante a carga.

```bash
python teste_carga.py                 # 20 req/s por 30 s
python teste_carga.py --modo disputa  # todos no mesmo produto (custo do lock)
```

`benchmark-algoritmos.md` e `.csv` são gerados por
[`estruturas/benchmark.py`](../../estruturas/benchmark.py), que compara os
quatro algoritmos de ordenação e as duas buscas próprias em cinco cenários de
entrada. Não precisa de Docker:

```bash
python -m estruturas.benchmark            # completo (~1 min)
python -m estruturas.benchmark --rapido   # menos repetições
```

O `evidencia_monitor.py` roda no CI a cada push
([`ci.yml`](../../.github/workflows/ci.yml)); o arquivo gerado fica anexado à
execução do workflow. Nenhum dos três é versionado aqui: a saída depende da máquina (núcleos,
memória, containers no ar), então cada arquivo é gerado na máquina de quem
apresenta, e o cabeçalho de cada um registra em que máquina rodou.
