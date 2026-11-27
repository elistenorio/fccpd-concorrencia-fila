import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")

# Pool dimensionado pelo benchmark de rede (evidencias/so/benchmark_rede.md):
# o padrão do SQLAlchemy (5 + 10 de overflow) esgotava com 100 usuários e as
# requisições esperavam até 30 s por uma conexão. 20 + 10 cabe folgado no
# max_connections = 100 do Postgres junto com recomendacoes, workers e
# reconciliador; pool_timeout curto faz a falta de conexão aparecer logo.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    pool_timeout=float(os.getenv("DB_POOL_TIMEOUT", "5")),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
