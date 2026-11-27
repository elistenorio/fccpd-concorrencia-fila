"""Rotas /estoque (app/estoque_rotas.py) com o TestClient: sem Postgres e sem Docker."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.estoque_rotas import roteador

app = FastAPI()
app.include_router(roteador)
cliente = TestClient(app)


@pytest.fixture(autouse=True)
def loja_limpa():
    cliente.post("/estoque/reiniciar?prioridade=true")


def cadastrar(codigo, nome, preco=1000):
    r = cliente.post("/estoque/produtos", json={"codigo": codigo, "nome": nome, "preco_centavos": preco})
    assert r.status_code == 201, r.text
    return r.json()


def test_fluxo_completo_com_desfazer():
    cadastrar("DOC-01", "Doce de caju", 1500)
    cadastrar("VAS-01", "Vaso de barro", 8900)
    r = cliente.post("/estoque/produtos/DOC-01/lotes", json={"quantidade": 5, "validade": "2027-01-10"})
    assert r.status_code == 201
    r = cliente.post("/estoque/produtos/DOC-01/lotes", json={"quantidade": 2, "validade": "2026-12-01"})
    assert [l["validade"] for l in r.json()["lotes"]] == ["2026-12-01", "2027-01-10"]  # inserção ordenada

    p1 = cliente.post("/estoque/pedidos", json={"codigo": "DOC-01", "quantidade": 3, "prioridade": 1}).json()
    p2 = cliente.post("/estoque/pedidos", json={"codigo": "DOC-01", "quantidade": 1, "prioridade": 5}).json()
    assert [p["id"] for p in cliente.get("/estoque/pedidos").json()] == [p2["id"], p1["id"]]

    r = cliente.post("/estoque/pedidos/processar", params={"todos": True, "hoje": "2026-11-26"}).json()
    assert r["atendidos"] == 2 and r["recusados"] == 0
    assert cliente.get("/estoque/produtos/DOC-01").json()["quantidade"] == 3
    assert cliente.get("/estoque").json()["pedidos_pendentes"] == 0

    assert cliente.post("/estoque/desfazer").json()["desfeito"] == f"pedido {p1['id']} atendido"
    assert cliente.get("/estoque/produtos/DOC-01").json()["quantidade"] == 6
    assert [p["id"] for p in cliente.get("/estoque/pedidos").json()] == [p1["id"]]
    assert cliente.get("/estoque/historico?n=2").json()[0]["tipo"] == "processar"


def test_buscas_e_vencidos():
    cadastrar("VAS-01", "Vaso de barro", 8900)
    cadastrar("VAS-02", "Vaso pintado", 12000)
    cadastrar("CES-01", "Cesto de palha", 4500)
    assert [p["codigo"] for p in cliente.get("/estoque/busca?prefixo=vaso").json()["produtos"]] == ["VAS-01", "VAS-02"]
    r = cliente.get("/estoque/faixa-preco?minimo=4000&maximo=9000").json()
    assert r["total"] == 2 and [p["codigo"] for p in r["produtos"]] == ["CES-01", "VAS-01"]
    cliente.patch("/estoque/produtos/CES-01/preco", json={"preco_centavos": 20000})
    assert [p["codigo"] for p in cliente.get("/estoque/faixa-preco?minimo=4000&maximo=9000").json()["produtos"]] == ["VAS-01"]
    cliente.post("/estoque/produtos/VAS-01/lotes", json={"quantidade": 1, "validade": "2026-01-01"})
    assert cliente.get("/estoque/vencidos?hoje=2026-11-26").json() == [
        {"codigo": "VAS-01", "nome": "Vaso de barro", "quantidade": 1, "validade": "2026-01-01"}]


def test_erros_viram_404_e_409():
    cadastrar("A", "Arte")
    assert cliente.post("/estoque/produtos", json={"codigo": "A", "nome": "x"}).status_code == 409
    assert cliente.get("/estoque/produtos/NADA").status_code == 404
    assert cliente.post("/estoque/pedidos", json={"codigo": "NADA", "quantidade": 1}).status_code == 404
    assert cliente.get("/estoque/pedidos/999").status_code == 404
    assert cliente.post("/estoque/produtos/A/saida", json={"quantidade": 1}).status_code == 409  # sem estoque
    assert cliente.post("/estoque/produtos/A/lotes", json={"quantidade": 0}).status_code == 422  # validação
    p = cliente.post("/estoque/pedidos", json={"codigo": "A", "quantidade": 1}).json()
    assert cliente.delete(f"/estoque/pedidos/{p['id']}").json()["status"] == "cancelado"
    assert cliente.delete(f"/estoque/pedidos/{p['id']}").status_code == 409  # já cancelado
    assert cliente.post("/estoque/pedidos/processar").json() is None
    cliente.post("/estoque/reiniciar")
    assert cliente.post("/estoque/desfazer").status_code == 409  # nada para desfazer


def test_requisicoes_simultaneas_nao_perdem_pedidos():
    """O FastAPI atende em várias threads; o Lock da rota mantém a fila consistente."""
    cadastrar("A", "Arte")
    cliente.post("/estoque/produtos/A/lotes", json={"quantidade": 400})

    def pedir(i):
        return cliente.post("/estoque/pedidos", json={"codigo": "A", "quantidade": 1, "prioridade": i % 5}).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert set(pool.map(pedir, range(400))) == {201}
    assert cliente.get("/estoque").json()["pedidos_pendentes"] == 400
    ids = [p["id"] for p in cliente.get("/estoque/pedidos?limite=1000").json()]
    assert sorted(ids) == list(range(1, 401))  # ids únicos, nenhum perdido

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: cliente.post("/estoque/pedidos/processar"), range(400)))
    assert cliente.get("/estoque/produtos/A").json()["quantidade"] == 0
    assert cliente.get("/estoque").json()["pedidos_pendentes"] == 0
