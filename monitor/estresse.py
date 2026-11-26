"""
Demonstração de OOM: aloca memória aos poucos até o kernel matar o processo.

    docker compose --profile demo up estresse-memoria

O serviço ``estresse-memoria`` do compose tem ``mem_limit: 128m`` e
``restart: "no"``. A cada passo este script aloca mais um bloco (e escreve
nele, senão o Linux não entrega as páginas de verdade). O painel mostra:

    nível 2  a barra de memória do container subindo → alerta de memória a
             partir de MONITOR_LIMITE_MEM (85%) → evento "oom" do Docker →
             container parado com código 137 (SIGKILL) e OOMKilled = true
    nível 1  o contador oom_kill do /proc/vmstat do kernel sobe

    python -m monitor.estresse [--passo-mb 4] [--intervalo 1] [--maximo-mb 1024]
"""

import argparse
import time


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--passo-mb", type=int, default=4)
    p.add_argument("--intervalo", type=float, default=1.0)
    p.add_argument("--maximo-mb", type=int, default=1024)
    a = p.parse_args()

    blocos = [None] * (a.maximo_mb // a.passo_mb + 1)
    usados = 0
    while usados < len(blocos):
        bloco = bytearray(a.passo_mb * 1024 * 1024)
        for i in range(0, len(bloco), 4096):  # toca cada página de 4 kB
            bloco[i] = 1
        blocos[usados] = bloco
        usados += 1
        print(f"alocados {usados * a.passo_mb} MB", flush=True)
        time.sleep(a.intervalo)
    print("chegou ao máximo sem ser morto (o container tem mem_limit?)", flush=True)


if __name__ == "__main__":
    main()
