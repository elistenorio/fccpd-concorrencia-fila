from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, text
from sqlalchemy.sql import func
from app.database import Base


class Produto(Base):
    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    estoque = Column(Integer, nullable=False, default=0)
    # Usados pelas recomendações: produtos da mesma categoria ou do mesmo
    # artesão ganham um bônus na similaridade.
    categoria = Column(String, nullable=True)
    artesao = Column(String, nullable=True)


class Pedido(Base):
    __tablename__ = "pedidos"

    id = Column(Integer, primary_key=True, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    # Opcional para não quebrar quem já compra só com produto_id (testes da Parte 1).
    cliente_id = Column(Integer, nullable=True, index=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String, nullable=False, default="pendente")


# O create_all só cria tabelas que não existem; não adiciona colunas novas em
# tabelas antigas. Estas instruções atualizam um banco criado na Parte 1 sem
# precisar apagar o volume (docker compose down -v). IF NOT EXISTS deixa tudo
# idempotente: rodar de novo não faz nada.
MIGRACOES = [
    "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS categoria VARCHAR",
    "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS artesao VARCHAR",
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_pedidos_cliente_id ON pedidos (cliente_id)",
]


def criar_e_migrar(engine):
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conexao:
        for instrucao in MIGRACOES:
            conexao.execute(text(instrucao))
