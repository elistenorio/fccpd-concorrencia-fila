# Evidências do PI3 — monitor e carga

`monitor.txt` é gerado por [`evidencia_monitor.py`](../../evidencia_monitor.py),
que roda com os containers de pé e verifica, com dados reais, o coletor
concorrente (4 threads e o buffer entre elas), os três níveis de coleta, a
queda e a volta de um serviço na linha do tempo de eventos e as estruturas
próprias em uso.

```bash
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

O `evidencia_monitor.py` roda no CI a cada push
([`ci.yml`](../../.github/workflows/ci.yml)); o arquivo gerado fica anexado à
execução do workflow. Como a saída depende da máquina (número de núcleos,
memória, containers no ar), ela é gerada na hora da demonstração em vez de ser
versionada aqui.
