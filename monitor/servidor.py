"""
Servidor do painel: API JSON + arquivos estáticos (monitor/static).

    uvicorn monitor.servidor:app --port 8002      (ou: python -m monitor)

Rotas:
    GET  /api/monitor?desde=<ts>&ordem=cpu|mem|nome|pid   estado do coletor
    POST /api/monitor/consumidor?pausado=true|false      pausa o consumidor (o buffer enche)
    POST /api/monitor/intervalo?segundos=0.5             ritmo dos produtores
    POST /api/corrida?produtores=3&consumidores=3&itens=2000&capacidade=16
    GET  /api/aed/{estrutura}                            retrato da estrutura
    POST /api/aed/{estrutura}/{operacao}?valor=...       chama o método real
    POST /api/aed/{estrutura}/reiniciar
    GET  /api/aed-ordenacao?n=300&tipo=aleatoria|ordenada|invertida|quase
    GET  /api/aed-busca?n=64&alvo=...
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from monitor.aed import ErroOperacao, Laboratorio, comparar_buscas, corrida_ordenacao
from monitor.coletor import Coletor
from monitor.corrida import executar_corrida

ESTATICOS = Path(__file__).parent / "static"

coletor = Coletor(
    intervalo=float(os.getenv("MONITOR_INTERVALO", "1")),
    limite_cpu=float(os.getenv("MONITOR_LIMITE_CPU", "80")),
)
laboratorio = Laboratorio()


@asynccontextmanager
async def ciclo_de_vida(_app):
    coletor.iniciar()
    yield
    coletor.parar()


app = FastAPI(title="Origem — monitor e laboratório", lifespan=ciclo_de_vida)


@app.middleware("http")
async def sem_cache_velho(request, call_next):
    # no-cache = o navegador revalida (ETag) a cada carga: editar o CSS/JS
    # aparece no próximo F5, sem precisar limpar o cache.
    resposta = await call_next(request)
    resposta.headers["Cache-Control"] = "no-cache"
    return resposta


# As rotas são "def" (não "async def"): o FastAPI roda cada uma numa thread do
# pool, então uma corrida de threads demorada não trava as outras requisições.

@app.get("/api/monitor")
def monitor(desde: float | None = None, ordem: str = "cpu", limite: int = Query(30, ge=1, le=500)):
    return coletor.instantaneo(desde=desde, ordem=ordem, limite=limite)


@app.post("/api/monitor/consumidor")
def pausar_consumidor(pausado: bool):
    coletor.pausar_consumidor(pausado)
    return {"pausado": pausado}


@app.post("/api/monitor/intervalo")
def mudar_intervalo(segundos: float = Query(..., ge=0.1, le=5)):
    coletor.intervalo = segundos
    return {"intervalo": segundos}


@app.post("/api/corrida")
def corrida(
    produtores: int = Query(3, ge=1, le=8),
    consumidores: int = Query(3, ge=1, le=8),
    itens: int = Query(2000, ge=10, le=10000),
    capacidade: int = Query(16, ge=1, le=256),
):
    return executar_corrida(produtores, consumidores, itens, capacidade)


@app.get("/api/aed-ordenacao")
def ordenacao(n: int = Query(300, ge=2, le=3000), tipo: str = "aleatoria"):
    if tipo not in ("aleatoria", "ordenada", "invertida", "quase"):
        raise HTTPException(422, "tipo inválido")
    return corrida_ordenacao(n, tipo)


@app.get("/api/aed-busca")
def busca(n: int = Query(64, ge=2, le=512), alvo: int | None = None):
    return comparar_buscas(n, alvo)


@app.get("/api/aed/{estrutura}")
def retrato(estrutura: str):
    try:
        return laboratorio.retrato(estrutura)
    except ErroOperacao as erro:
        raise HTTPException(404, str(erro)) from erro


@app.post("/api/aed/{estrutura}/reiniciar")
def reiniciar(estrutura: str):
    try:
        return laboratorio.reiniciar(estrutura)
    except ErroOperacao as erro:
        raise HTTPException(404, str(erro)) from erro


@app.post("/api/aed/{estrutura}/{operacao}")
def operar(estrutura: str, operacao: str, valor: str | None = None):
    if valor is not None and estrutura in ("lista", "pilha", "fila", "anel"):
        valor = int(valor) if valor.lstrip("-").isdigit() else valor
    try:
        return laboratorio.operar(estrutura, operacao, valor)
    except ErroOperacao as erro:
        raise HTTPException(409, str(erro)) from erro


@app.get("/")
def pagina():
    return FileResponse(ESTATICOS / "index.html")


app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
