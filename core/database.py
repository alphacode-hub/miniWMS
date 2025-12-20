# core/database.py
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from core.config import settings


# =========================================================
# ENGINE / SESSION
# =========================================================

DATABASE_URL = settings.DATABASE_URL

# SQLite (local) requiere check_same_thread=False
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)

Base = declarative_base()


# =========================================================
# DEPENDENCY
# =========================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================================================
# INIT DB
# =========================================================

def init_db() -> None:
    """
    Importa modelos SOLO aquí (lazy) para registrar todas las tablas en Base,
    evitando imports circulares entre database <-> models <-> services.
    """
    # Importa el paquete de modelos (registra todos los modelos en Base.metadata)
    import core.models  # noqa: F401

    # Si tienes submódulos que no se importan automáticamente desde core.models,
    # impórtalos aquí también (solo si aplica).
    # Ej:
    # import core.models.inbound  # noqa: F401

    Base.metadata.create_all(bind=engine)
