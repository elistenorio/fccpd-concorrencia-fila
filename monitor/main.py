"""
Painel de monitoramento (porta 8002).

Serve as três telas (hardware/SO, containers e serviços) e a API JSON que elas
consultam. O coletor sobe junto com a aplicação (``lifespan``) e vive no mesmo
processo: as telas leem o ``EstadoMonitor`` que as threads do coletor alimentam,
sem banco no meio.

Cada requisição é atendida numa thread do uvicorn, então o estado é lido de
várias threads ao mesmo tempo; toda leitura passa pelo lock do
``EstadoMonitor``.
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from monitor.coletor import Coletor
from monitor.eventos import LIMITE_MEMORIA_HOST_PCT, LIMITE_MEMORIA_PCT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("monitor")

WEB = Path(__file__).parent / "web"

coletor = Coletor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    coletor.iniciar()
    try:
        yield
    finally:
        coletor.parar()


app = FastAPI(title="Origem — Painel de monitoramento", lifespan=lifespan)


def _serie(amostras, extrair):
    """Série temporal compacta para os gráficos: pares (timestamp, valor)."""
    pontos = []
    for amostra in amostras:
        valor = extrair(amostra)
        if valor is not None:
            pontos.append([round(amostra.get("timestamp", 0), 1), valor])
    return pontos


@app.get("/")
def raiz():
    return RedirectResponse("/painel/")


@app.get("/saude")
def saude():
    """Health check do próprio painel, usado pelo compose."""
    return {"status": "ok", "coletor_vivo": coletor.vivo}


@app.get("/api/resumo")
def resumo():
    return {
        "agora": time.time(),
        "estado": coletor.estado.resumo(),
        "coletor": {
            "threads": coletor.threads_vivas(),
            "estatisticas": coletor.estatisticas,
            "buffer": {"ocupado": len(coletor.buffer), "capacidade": coletor.buffer.capacidade},
        },
        "limites": {
            "memoria_container_pct": LIMITE_MEMORIA_PCT,
            "memoria_host_pct": LIMITE_MEMORIA_HOST_PCT,
        },
    }


@app.get("/api/nivel1")
def nivel1(janela: int = Query(60, ge=1, le=600)):
    """Tela 1 — hardware e SO: última amostra e as séries dos gráficos."""
    ultima = coletor.estado.ultima(1)
    historico = coletor.estado.historico(1, limite=janela)
    return {
        "amostra": ultima,
        "series": {
            "cpu_total": _serie(historico, lambda a: a.get("cpu_total_pct")),
            "memoria": _serie(historico, lambda a: (a.get("memoria") or {}).get("uso_pct")),
            "threads": _serie(historico, lambda a: a.get("total_threads")),
        },
    }


@app.get("/api/nivel2")
def nivel2(ordenar_por: str = Query("cpu", enum=["cpu", "memoria", "nome"])):
    """Tela 2 — containers."""
    ultima = coletor.estado.ultima(2) or {}
    return {
        "disponivel": ultima.get("disponivel", False),
        "motivo": ultima.get("motivo"),
        "timestamp": ultima.get("timestamp"),
        "containers": coletor.estado.containers(ordenar_por=ordenar_por),
        "limite_memoria_pct": LIMITE_MEMORIA_PCT,
    }


@app.get("/api/nivel3")
def nivel3(minutos: int = Query(15, ge=1, le=120)):
    """Tela 3 — serviços, com a linha do tempo de eventos do período."""
    desde = time.time() - minutos * 60
    eventos = coletor.estado.eventos(desde=desde, limite=100)
    return {
        "servicos": coletor.estado.servicos(),
        "eventos": [e.para_dict() for e in eventos],
        "janela_min": minutos,
    }


@app.get("/api/eventos")
def eventos(
    minutos: int = Query(15, ge=1, le=1440),
    limite: int = Query(50, ge=1, le=500),
    severidade: str | None = Query(None, enum=["info", "alerta", "critico"]),
):
    desde = time.time() - minutos * 60
    encontrados = coletor.estado.eventos(desde=desde, limite=limite, severidade=severidade)
    return {"eventos": [e.para_dict() for e in encontrados], "total": len(encontrados)}


@app.get("/api/alertas")
def alertas(maximo: int = Query(20, ge=1, le=100)):
    """
    Retira os alertas da fila. Chamado pelo painel para a faixa de avisos: cada
    alerta é entregue uma vez só (a fila é consumida), e nenhum se perde pelo
    caminho, porque não tem limite de tamanho.
    """
    retirados = coletor.estado.alertas_pendentes(maximo)
    return {"alertas": [e.para_dict() for e in retirados]}


@app.get("/api/containers/{nome}")
def container(nome: str):
    encontrado = coletor.estado.container(nome)
    if encontrado is None:
        raise HTTPException(status_code=404, detail="Container não monitorado")
    return encontrado


@app.get("/api/processos/{nome}")
def processo(nome: str):
    encontrado = coletor.estado.processo_por_nome(nome)
    if encontrado is None:
        raise HTTPException(status_code=404, detail="Processo não encontrado na última amostra")
    return encontrado


@app.get("/api/historico/nivel1")
def historico_nivel1(desde: float | None = Query(None, description="timestamp Unix")):
    """
    Amostras a partir de um horário. Com ``desde``, o corte é achado por busca
    binária na janela (que está em ordem crescente de tempo).
    """
    amostras = coletor.estado.historico(1, desde=desde)
    return {"total": len(amostras), "amostras": amostras}


# O painel é HTML estático + fetch na API acima: sem build, sem framework de
# front e sem dependência nova no requirements.
if WEB.is_dir():
    app.mount("/painel", StaticFiles(directory=str(WEB), html=True), name="painel")
else:  # pragma: no cover - só se a imagem for montada sem a pasta web
    logger.warning("Pasta do painel não encontrada em %s", WEB)
