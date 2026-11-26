"""
Rotas /estoque: a api consumindo o modelo de domínio de AED (dominio/).

O estado fica em memória numa Loja (estruturas próprias: lista, hash, árvore,
busca binária, lista ordenada, fila e pilha). É um módulo de gestão de estoque
por lotes ao lado do checkout da Parte 1, que continua no Postgres.
POST /estoque/importar copia o catálogo do Postgres para a Loja.

Concorrência: o FastAPI roda rotas "def" num pool de threads, e as estruturas
próprias não são thread-safe (ex.: dois inserir() na mesma ListaOrdenada podem
perder um nó). Toda rota pega o mesmo Lock antes de mexer na Loja.

Erros: produto/pedido inexistente -> 404; regra de negócio (estoque
insuficiente, código repetido, nada para desfazer...) -> 409.
"""

import threading
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from dominio import ATENDIDO, ErroEstoque, Loja, Produto, ProdutoInexistente

roteador = APIRouter(prefix="/estoque", tags=["estoque (AED)"])

_lock = threading.Lock()
_loja = Loja(prioridade=True)


# ------------------------------------------------------------------ esquemas

class ProdutoIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=40)
    nome: str = Field(min_length=1, max_length=120)
    categoria: str = ""
    artesao: str = ""
    preco_centavos: int = Field(0, ge=0)


class LoteIn(BaseModel):
    quantidade: int = Field(gt=0)
    validade: date | None = None


class SaidaIn(BaseModel):
    quantidade: int = Field(gt=0)
    hoje: date | None = None


class PrecoIn(BaseModel):
    preco_centavos: int = Field(ge=0)


class PedidoIn(BaseModel):
    codigo: str
    quantidade: int = Field(gt=0)
    prioridade: int = 0
    cliente: str | None = None


# ------------------------------------------------------------------ JSON

def produto_json(p, hoje=None):
    return {
        "codigo": p.codigo, "nome": p.nome, "categoria": p.categoria, "artesao": p.artesao,
        "preco_centavos": p.preco_centavos, "quantidade": p.quantidade,
        "disponivel": p.disponivel(hoje or date.today()),
        "lotes": [{"quantidade": l.quantidade, "validade": l.validade} for l in p.lotes],
    }


def pedido_json(p):
    return {"id": p.id, "codigo": p.codigo, "quantidade": p.quantidade, "prioridade": p.prioridade,
            "cliente": p.cliente, "status": p.status, "motivo": p.motivo}


def _executar(funcao):
    """Roda ``funcao`` com o Lock e traduz os erros do domínio para HTTP."""
    with _lock:
        try:
            return funcao()
        except ProdutoInexistente as erro:
            raise HTTPException(404, str(erro)) from erro
        except ErroEstoque as erro:
            codigo = 404 if "não existe" in str(erro) else 409
            raise HTTPException(codigo, str(erro)) from erro
        except ValueError as erro:
            raise HTTPException(409, str(erro)) from erro


# ------------------------------------------------------------------ rotas

@roteador.get("")
def resumo():
    def f():
        proximo = _loja.pedidos.espiar()
        return {"produtos": len(_loja.estoque), "pedidos_pendentes": len(_loja.pedidos),
                "fila_com_prioridade": _loja.pedidos.prioridade, "operacoes_no_historico": len(_loja.historico),
                "proximo_pedido": pedido_json(proximo) if proximo else None}
    return _executar(f)


@roteador.post("/reiniciar")
def reiniciar(prioridade: bool = True):
    global _loja
    with _lock:
        _loja = Loja(prioridade=prioridade)
    return {"ok": True, "fila_com_prioridade": prioridade}


def _sessao():
    # Import tardio: o resto do roteador (e os testes) não precisa do Postgres.
    from app.database import get_db
    yield from get_db()


@roteador.post("/importar")
def importar(limite: int = Query(5000, ge=1, le=100_000), db=Depends(_sessao)):
    """Copia o catálogo do Postgres (produtos da loja) para uma Loja nova.

    Cada produto vira um código "ORI-<id>" com o estoque atual como um lote sem
    validade. O banco não tem preço, então o preço começa em 0 (PATCH .../preco).
    """
    from app.models import Produto as ProdutoBD

    linhas = db.query(ProdutoBD).order_by(ProdutoBD.id).limit(limite).all()
    global _loja
    with _lock:
        nova = Loja(prioridade=_loja.pedidos.prioridade)
        for linha in linhas:
            codigo = f"ORI-{linha.id}"
            # Direto no Estoque (e não pela Loja): a importação não entra no histórico do desfazer.
            nova.estoque.cadastrar(Produto(codigo, linha.nome, linha.categoria or "", linha.artesao or ""))
            if linha.estoque > 0:
                nova.estoque.entrada(codigo, linha.estoque)
        _loja = nova
    return {"importados": len(linhas)}


@roteador.post("/produtos", status_code=201)
def cadastrar(p: ProdutoIn):
    return _executar(lambda: produto_json(_loja.cadastrar(p.codigo, p.nome, p.categoria, p.artesao, p.preco_centavos)))


@roteador.get("/produtos/{codigo}")
def consultar(codigo: str):
    """Índice por hash: O(1) médio."""
    return _executar(lambda: produto_json(_loja.estoque.consultar(codigo)))


@roteador.delete("/produtos/{codigo}")
def remover(codigo: str):
    return _executar(lambda: produto_json(_loja.remover_produto(codigo)))


@roteador.patch("/produtos/{codigo}/preco")
def alterar_preco(codigo: str, corpo: PrecoIn):
    def f():
        _loja.alterar_preco(codigo, corpo.preco_centavos)
        return produto_json(_loja.estoque.consultar(codigo))
    return _executar(f)


@roteador.post("/produtos/{codigo}/lotes", status_code=201)
def entrada(codigo: str, lote: LoteIn):
    """Inserção ordenada por validade."""
    def f():
        _loja.entrada(codigo, lote.quantidade, lote.validade)
        return produto_json(_loja.estoque.consultar(codigo))
    return _executar(f)


@roteador.post("/produtos/{codigo}/saida")
def saida(codigo: str, corpo: SaidaIn):
    """FEFO: tira primeiro do lote que vence antes; tudo ou nada (409 se faltar)."""
    def f():
        retiradas = _loja.saida(codigo, corpo.quantidade, corpo.hoje or date.today())
        return {"retirado_de": [{"validade": r.lote.validade, "quantidade": r.quantidade} for r in retiradas],
                "produto": produto_json(_loja.estoque.consultar(codigo))}
    return _executar(f)


@roteador.get("/busca")
def busca(prefixo: str = Query(..., min_length=1), limite: int = Query(50, ge=1, le=1000)):
    """Árvore binária de busca por nome: O(h + k)."""
    def f():
        achados = _loja.estoque.buscar_prefixo(prefixo)
        return {"total": len(achados), "produtos": [produto_json(p) for p in achados[:limite]]}
    return _executar(f)


@roteador.get("/faixa-preco")
def faixa_preco(minimo: int = Query(..., ge=0), maximo: int = Query(..., ge=0), limite: int = Query(50, ge=1, le=1000)):
    """Busca binária no catálogo ordenado por preço: O(log n + k). Valores em centavos."""
    def f():
        achados = _loja.estoque.faixa_de_preco(minimo, maximo)
        return {"total": len(achados), "produtos": [produto_json(p) for p in achados[:limite]]}
    return _executar(f)


@roteador.get("/vencidos")
def vencidos(hoje: date | None = None):
    def f():
        dia = hoje or date.today()
        return [{"codigo": p.codigo, "nome": p.nome, "quantidade": l.quantidade, "validade": l.validade}
                for p, l in _loja.estoque.vencidos(dia)]
    return _executar(f)


@roteador.post("/pedidos", status_code=201)
def registrar_pedido(p: PedidoIn):
    return _executar(lambda: pedido_json(_loja.registrar_pedido(p.codigo, p.quantidade, p.prioridade, p.cliente)))


@roteador.get("/pedidos")
def listar_pedidos(limite: int = Query(100, ge=1, le=1000)):
    """Pendentes na ordem em que serão atendidos."""
    def f():
        resultado = []
        for p in _loja.pedidos:
            if len(resultado) == limite:
                break
            resultado.append(pedido_json(p))
        return resultado
    return _executar(f)


@roteador.get("/pedidos/{id_}")
def consultar_pedido(id_: int):
    return _executar(lambda: pedido_json(_loja.pedido(id_)))


@roteador.delete("/pedidos/{id_}")
def cancelar_pedido(id_: int):
    def f():
        pedido = _loja.pedido(id_)
        _loja.cancelar_pedido(pedido)
        return pedido_json(pedido)
    return _executar(f)


@roteador.post("/pedidos/processar")
def processar(todos: bool = False, hoje: date | None = None):
    """Atende o próximo pedido da fila (ou todos). 200 com null se a fila estiver vazia."""
    def f():
        dia = hoje or date.today()
        if not todos:
            pedido = _loja.processar_proximo(dia)
            return pedido_json(pedido) if pedido else None
        atendidos = []
        while True:
            pedido = _loja.processar_proximo(dia)
            if pedido is None:
                break
            atendidos.append(pedido_json(pedido))
        return {"atendidos": sum(1 for p in atendidos if p["status"] == ATENDIDO),
                "recusados": sum(1 for p in atendidos if p["status"] != ATENDIDO), "pedidos": atendidos}
    return _executar(f)


@roteador.post("/desfazer")
def desfazer():
    """Desempilha a última operação do histórico (Pilha) e aplica o inverso."""
    return _executar(lambda: {"desfeito": _loja.desfazer().descricao})


@roteador.get("/historico")
def historico(n: int = Query(10, ge=1, le=200)):
    return _executar(lambda: [{"tipo": op.tipo, "descricao": op.descricao} for op in _loja.ultimas_operacoes(n)])
