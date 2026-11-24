# Evidências do PI3 — monitor

`monitor.txt` é gerado por [`evidencia_monitor.py`](../../evidencia_monitor.py),
que roda com os containers de pé e verifica, com dados reais, o coletor
concorrente (4 threads e o buffer entre elas), os três níveis de coleta, a
queda e a volta de um serviço na linha do tempo de eventos e as estruturas
próprias em uso.

```bash
docker compose up -d --build --wait
python evidencia_monitor.py
```

O mesmo script roda no CI a cada push
([`ci.yml`](../../.github/workflows/ci.yml)); o arquivo gerado fica anexado à
execução do workflow. Como a saída depende da máquina (número de núcleos,
memória, containers no ar), ela é gerada na hora da demonstração em vez de ser
versionada aqui.
