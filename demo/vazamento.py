"""
Demonstração ao vivo de OOM (PI3, bônus).

Aloca memória aos poucos, de propósito, até o kernel matar o processo. Serve
para mostrar o painel reagindo em tempo real: a barra de memória do container
sobe, o alerta de 80% dispara, o container é morto e aparece como `OOMKilled`
na tela 2 e como evento crítico na linha do tempo.

**Não sobe junto com o resto**: o serviço `vazamento` está no perfil `demo` do
Compose, então só roda quando pedido explicitamente.

    docker compose --profile demo up -d --build vazamento

Com o limite padrão de 128 MB e os valores padrão abaixo, o container morre em
cerca de 50 segundos, devagar o suficiente para dar para acompanhar na tela.

A memória precisa ser **escrita**, e não só reservada: o cgroup conta páginas
que foram realmente tocadas. Um `bytearray(n)` já nasce zerado página a página,
então contabiliza; uma lista vazia pré-alocada não contabilizaria.

Variáveis de ambiente:
    DEMO_PASSO_MB   quanto alocar por vez (padrão 4)
    DEMO_PAUSA_S    pausa entre as alocações (padrão 1.5)
    DEMO_TETO_MB    para antes de chegar ao limite, se quiser mostrar só o
                    alerta sem matar o container (padrão 0 = sem teto)
"""

import os
import sys
import time

PASSO_MB = int(os.getenv("DEMO_PASSO_MB", "4"))
PAUSA_S = float(os.getenv("DEMO_PAUSA_S", "1.5"))
TETO_MB = int(os.getenv("DEMO_TETO_MB", "0"))

MB = 1024 * 1024


def _ler_numero(caminho):
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            bruto = arquivo.read().strip()
    except (FileNotFoundError, PermissionError):
        return None
    if bruto in ("max", ""):
        return None
    try:
        valor = int(bruto)
    except ValueError:
        return None
    # No cgroup v1, "sem limite" é um número gigante, não a palavra "max".
    return None if valor > 2**60 else valor


def memoria_do_cgroup():
    """
    Uso e limite de memória do próprio container. É a mesma conta que o Docker
    mostra, então o número impresso aqui bate com o do painel (a menos do cache,
    que o painel desconta). Tenta cgroup v2 e cai para o v1 em hosts antigos.
    """
    uso = _ler_numero("/sys/fs/cgroup/memory.current")
    limite = _ler_numero("/sys/fs/cgroup/memory.max")
    if uso is None:
        uso = _ler_numero("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        limite = _ler_numero("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    return uso, limite


def main():
    blocos = []
    alocado_mb = 0
    print(
        f"[vazamento] alocando {PASSO_MB} MB a cada {PAUSA_S}s"
        + (f", até {TETO_MB} MB" if TETO_MB else ", até o kernel matar o processo"),
        flush=True,
    )

    while True:
        if TETO_MB and alocado_mb >= TETO_MB:
            print(f"[vazamento] teto de {TETO_MB} MB atingido; segurando a memória", flush=True)
            while True:
                time.sleep(60)

        # bytearray, e não uma lista: precisa ser memória escrita de verdade.
        blocos.append(bytearray(PASSO_MB * MB))
        alocado_mb += PASSO_MB

        uso, limite = memoria_do_cgroup()
        if uso is not None and limite:
            print(
                f"[vazamento] alocados {alocado_mb} MB | cgroup {uso / MB:.0f} MB "
                f"de {limite / MB:.0f} MB ({100 * uso / limite:.0f}%)",
                flush=True,
            )
        else:
            print(f"[vazamento] alocados {alocado_mb} MB", flush=True)

        time.sleep(PAUSA_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[vazamento] interrompido", flush=True)
        sys.exit(0)
    except MemoryError:
        # No container com mem_limit isto NÃO deve acontecer: quem mata o
        # processo é o kernel (SIGKILL), e é esse kill que o Docker registra
        # como OOMKilled. MemoryError aqui significa que a alocação esbarrou
        # noutro teto antes (ulimit -v, ou rodar fora do Docker), e nesse caso
        # o container sai com erro comum e a tela 2 não marca OOMKilled.
        print(
            "[vazamento] MemoryError: a alocação falhou antes do limite do cgroup. "
            "Rodando fora do Docker ou com ulimit -v? Para a demonstração de OOM, "
            "use: docker compose --profile demo up -d --build vazamento",
            flush=True,
        )
        sys.exit(1)
