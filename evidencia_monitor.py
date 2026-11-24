"""
Evidência de que o monitor coleta os três níveis de verdade (PI3, etapas 4 e 5).

Roda no host, com os containers de pé:
    docker compose up -d --build --wait
    python evidencia_monitor.py

Gera evidencias/pi3/monitor.txt com, para cada verificação, o que o painel
estava respondendo no momento:

1. Coletor concorrente: as 4 threads vivas (3 produtoras + 1 consumidora) e o
   buffer entre elas recebendo amostras dos três níveis.
2. Nível 1 (hardware/SO, lido de /proc): CPU por núcleo, memória, disco,
   processos e threads.
3. Nível 2 (containers, pela API da Docker Engine): status, CPU, memória e
   limite, reinícios e OOMKilled de cada container do Compose.
4. Nível 3 (serviços): health check, tempo de resposta e tempo no ar.
5. Eventos e alertas: derruba um serviço de verdade (docker compose stop
   recomendacoes), confirma que o evento aparece na linha do tempo, religa e
   confirma o evento de volta.
6. Estruturas próprias em uso: janela fixa de amostras, árvore de eventos (e a
   altura dela, que o rebalanceamento mantém perto de log2 n) e busca binária
   no histórico.

Sai com código != 0 se alguma verificação falhar.
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent
SAIDA = RAIZ / "evidencias" / "pi3" / "monitor.txt"
MONITOR = "http://127.0.0.1:8002"
HTTP = httpx.Client(timeout=10.0)
# Tempo para o coletor dar pelo menos uma volta nos três níveis (o mais lento
# coleta a cada 5 s).
CICLO = 14

linhas_saida = []
falhas = []


def escrever(texto=""):
    print(texto)
    linhas_saida.append(texto)


def secao(titulo):
    escrever(f"\n=== {titulo} ===")


def checar(condicao, descricao):
    escrever(f"  [{'OK' if condicao else 'FALHOU'}] {descricao}")
    if not condicao:
        falhas.append(descricao)


def compose(*args):
    return subprocess.run(["docker", "compose", *args], capture_output=True, text=True, cwd=RAIZ).stdout


def buscar(caminho):
    return HTTP.get(f"{MONITOR}{caminho}").json()


def formatar_bytes(valor):
    if valor is None:
        return "?"
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if abs(valor) < 1024 or unidade == "TB":
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return "?"


def esperar_coletor(limite=60):
    """O painel sobe antes da primeira amostra; espera os três níveis chegarem."""
    fim = time.time() + limite
    while time.time() < fim:
        try:
            if HTTP.get(f"{MONITOR}/saude").json().get("coletor_vivo"):
                resumo = buscar("/api/resumo")
                nivel2 = buscar("/api/nivel2")
                nivel3 = buscar("/api/nivel3")
                if resumo["estado"]["amostras_recebidas"] >= 3 and nivel2["containers"] and nivel3["servicos"]:
                    return True
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        time.sleep(2)
    return False


def verificar_coletor():
    secao("1. COLETOR CONCORRENTE")
    resumo = buscar("/api/resumo")
    coletor = resumo["coletor"]
    for thread in coletor["threads"]:
        escrever(f"  thread {thread['nome']}: {'viva' if thread['viva'] else 'MORTA'}")
    estatisticas = coletor["estatisticas"]
    escrever(
        f"  coletas: nível 1 = {estatisticas['nivel1']['coletas']}, "
        f"nível 2 = {estatisticas['nivel2']['coletas']}, nível 3 = {estatisticas['nivel3']['coletas']}"
    )
    escrever(
        f"  consumidor: {estatisticas['consumidor']['aplicadas']} amostras aplicadas, "
        f"{estatisticas['consumidor']['eventos']} eventos gerados"
    )
    escrever(f"  buffer: {coletor['buffer']['ocupado']}/{coletor['buffer']['capacidade']} posições ocupadas")

    checar(len(coletor["threads"]) == 4, "4 threads (3 produtoras + 1 consumidora)")
    checar(all(t["viva"] for t in coletor["threads"]), "todas as threads vivas")
    checar(
        all(estatisticas[n]["coletas"] > 0 for n in ("nivel1", "nivel2", "nivel3")),
        "os três níveis coletaram pelo menos uma vez",
    )
    checar(estatisticas["consumidor"]["aplicadas"] > 0, "o consumidor retirou amostras do buffer")


def verificar_nivel1():
    secao("2. NÍVEL 1 — HARDWARE E SO (/proc)")
    dados = buscar("/api/nivel1")
    amostra = dados["amostra"]
    memoria = amostra["memoria"]
    escrever(f"  CPU total: {amostra['cpu_total_pct']}% em {len(amostra['cpus'])} núcleo(s)")
    for cpu in amostra["cpus"][:8]:
        escrever(f"    {cpu['nome']}: {cpu['uso_pct']}%")
    escrever(
        f"  Memória: {memoria['uso_pct']}% "
        f"({formatar_bytes(memoria['usada_bytes'])} de {formatar_bytes(memoria['total_bytes'])})"
    )
    escrever(f"  Disco: {amostra['disco']['uso_pct']}% de {formatar_bytes(amostra['disco']['total_bytes'])}")
    escrever(f"  Processos: {amostra['total_processos']} | threads: {amostra['total_threads']}")
    escrever(f"  Coleta levou {amostra['duracao_coleta_ms']} ms")
    escrever("  Maiores processos por CPU (quick sort próprio):")
    for processo in amostra["processos"][:5]:
        escrever(
            f"    {processo['pid']:>7} {processo['nome'][:28]:<28} "
            f"cpu {processo['cpu_pct']}% | rss {formatar_bytes(processo['rss_bytes'])} | "
            f"{processo['threads']} thread(s) | {processo['estado']}"
        )

    checar(len(amostra["cpus"]) >= 1, "CPU por núcleo")
    checar(memoria["total_bytes"] > 0, "memória total lida de /proc/meminfo")
    checar(amostra["disco"]["total_bytes"] > 0, "uso de disco")
    checar(amostra["total_processos"] > 0 and amostra["total_threads"] > 0, "processos e threads")
    checar(len(dados["series"]["cpu_total"]) >= 2, "série histórica de CPU com mais de um ponto")


def verificar_nivel2():
    secao("3. NÍVEL 2 — CONTAINERS (API DA DOCKER ENGINE)")
    dados = buscar("/api/nivel2?ordenar_por=memoria")
    checar(dados["disponivel"], f"socket do Docker acessível ({dados.get('motivo') or 'ok'})")
    escrever(f"  {len(dados['containers'])} containers (ordenados por memória, quick sort próprio):")
    for container in dados["containers"]:
        memoria = container["memoria"]
        escrever(
            f"    {container['nome'][:32]:<32} {container['status']:<10} "
            f"cpu {container['cpu_pct']}% | mem {formatar_bytes(memoria['usada_bytes'])}/"
            f"{formatar_bytes(memoria['limite_bytes'])} ({memoria['uso_pct']}%) | "
            f"reinícios {container['reinicios']} | OOMKilled {container['oom_killed']}"
        )

    checar(len(dados["containers"]) >= 3, "containers do Compose encontrados")
    checar(
        any(c["memoria"]["usada_bytes"] for c in dados["containers"]),
        "uso de memória por container",
    )
    checar(
        all(c["reinicios"] is not None and c["oom_killed"] is not None for c in dados["containers"]),
        "reinícios e OOMKilled lidos de todos os containers",
    )


def verificar_nivel3():
    secao("4. NÍVEL 3 — SERVIÇOS (HEALTH CHECK)")
    dados = buscar("/api/nivel3")
    for servico in dados["servicos"]:
        escrever(
            f"    {servico['nome']:<16} {servico['estado']:<10} "
            f"HTTP {servico['codigo']} em {servico['tempo_resposta_ms']} ms | "
            f"nesse estado há {servico['tempo_no_ar_s']}s"
        )
    checar(len(dados["servicos"]) >= 2, "serviços monitorados")
    checar(all(s["estado"] == "no ar" for s in dados["servicos"]), "todos os serviços no ar antes do teste")


def verificar_eventos():
    secao("5. EVENTOS — QUEDA E VOLTA DE UM SERVIÇO")
    escrever("  $ docker compose stop recomendacoes")
    compose("stop", "recomendacoes")
    time.sleep(CICLO)
    dados = buscar("/api/nivel3")
    caiu = [s for s in dados["servicos"] if s["nome"] == "recomendacoes"]
    escrever(f"  estado do serviço: {caiu[0]['estado'] if caiu else '?'}")
    eventos_queda = [e for e in dados["eventos"] if "recomendacoes" in e["mensagem"]]
    for evento in eventos_queda[:3]:
        escrever(f"    [{evento['severidade']}] {evento['mensagem']}")
    checar(bool(caiu) and caiu[0]["estado"] != "no ar", "o monitor detectou o serviço fora do ar")
    checar(
        any(e["severidade"] == "critico" for e in eventos_queda),
        "evento crítico registrado na linha do tempo",
    )

    escrever("  $ docker compose start recomendacoes")
    compose("start", "recomendacoes")
    time.sleep(CICLO + 6)
    dados = buscar("/api/nivel3")
    voltou = [s for s in dados["servicos"] if s["nome"] == "recomendacoes"]
    escrever(f"  estado do serviço: {voltou[0]['estado'] if voltou else '?'}")
    eventos_volta = [e for e in dados["eventos"] if "-> no ar" in e["mensagem"]]
    for evento in eventos_volta[:3]:
        escrever(f"    [{evento['severidade']}] {evento['mensagem']}")
    checar(bool(voltou) and voltou[0]["estado"] == "no ar", "o serviço voltou e o monitor percebeu")
    checar(bool(eventos_volta), "evento de retorno registrado")


def verificar_estruturas():
    secao("6. ESTRUTURAS PRÓPRIAS EM USO")
    resumo = buscar("/api/resumo")["estado"]
    escrever(f"  janela de amostras (FilaCircular): {resumo['janela_amostras']} posições, memória fixa")
    escrever(f"  amostras recebidas até agora: {resumo['amostras_recebidas']}")
    escrever(f"  containers na TabelaHash: {resumo['containers_conhecidos']}")
    escrever(
        f"  eventos na ArvoreBusca: {resumo['eventos_guardados']} "
        f"(altura {resumo['altura_arvore_eventos']})"
    )
    escrever(f"  alertas ainda na Fila: {resumo['alertas_na_fila']}")

    # Busca binária: pede o histórico a partir de um horário no meio da janela.
    agora = time.time()
    completo = buscar("/api/historico/nivel1")
    recorte = buscar(f"/api/historico/nivel1?desde={agora - 10}")
    escrever(f"  histórico completo: {completo['total']} amostras")
    escrever(f"  histórico dos últimos 10 s (busca binária no corte): {recorte['total']} amostras")

    checar(resumo["eventos_guardados"] > 0, "eventos guardados na árvore")
    checar(recorte["total"] <= completo["total"], "o corte por horário devolve um subconjunto")
    if resumo["eventos_guardados"] > 8:
        # Chaves crescentes degenerariam a BST (altura = n); o rebalanceamento
        # mantém a altura perto do ideal.
        ideal = resumo["eventos_guardados"].bit_length()
        checar(
            resumo["altura_arvore_eventos"] <= 2 * ideal,
            f"árvore de eventos não degenerou (altura {resumo['altura_arvore_eventos']}, ideal ~{ideal})",
        )


def main():
    escrever("EVIDÊNCIA DO MONITOR — PI3 (coletor concorrente e painel)")
    escrever(f"Gerado em: {datetime.now().astimezone():%d/%m/%Y %H:%M:%S (UTC%z)}")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=RAIZ)
    escrever(f"Commit: {commit.stdout.strip()}")
    escrever("\nContainers:")
    for linha in compose("ps", "--format", "{{.Service}}\t{{.Status}}").splitlines():
        escrever("  " + linha)

    if not esperar_coletor():
        escrever("\nO coletor não ficou pronto a tempo. O painel está no ar em " + MONITOR + "?")
        falhas.append("coletor não ficou pronto")
    else:
        verificar_coletor()
        verificar_nivel1()
        verificar_nivel2()
        verificar_nivel3()
        verificar_eventos()
        verificar_estruturas()

    secao("RESULTADO")
    escrever("Todas as verificações OK" if not falhas else f"{len(falhas)} verificação(ões) falharam:")
    for falha in falhas:
        escrever(f"  - {falha}")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas_saida) + "\n", encoding="utf-8")
    print(f"\nSalvo em {SAIDA.relative_to(RAIZ)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
