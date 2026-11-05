from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer
from sqlalchemy.sql import func

from app.database import Base


class Recomendacao(Base):
    """
    Recomendações pré-calculadas. Esta tabela é do serviço de recomendações:
    só ele escreve aqui. De pedidos e produtos (da api) ele só lê.
    """

    __tablename__ = "recomendacoes"

    produto_id = Column(Integer, ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True)
    recomendado_id = Column(Integer, ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True)
    posicao = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    calculado_em = Column(DateTime(timezone=True), server_default=func.now())
