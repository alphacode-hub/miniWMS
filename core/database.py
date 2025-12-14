# core/database.py
"""
Capa de base de datos (SQLAlchemy) – ORBION (SaaS enterprise)

✔ SQLite (dev/demo) + PostgreSQL (prod)
✔ Pooling y estabilidad enterprise
✔ get_db() estándar FastAPI
✔ init_db() controlado por entorno
✔ seed_superadmin() SIEMPRE operativo (caso Orbion: superadmin dueño)
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from core.config import settings
from core.logging_config import logger

# ============================
#   ENGINE
# ============================

def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _build_engine():
    engine_kwargs = {
        "pool_pre_ping": True,
        "echo": settings.APP_DEBUG,
        "future": True,
    }

    if _is_sqlite(settings.DATABASE_URL):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        engine_kwargs.update(
            {
                "pool_size": 10,
                "max_overflow": 20,
                "pool_timeout": 30,
                "pool_recycle": 1800,
            }
        )

    return create_engine(settings.DATABASE_URL, **engine_kwargs)


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

Base = declarative_base()


# ============================
#   DEPENDENCY FASTAPI
# ============================

def get_db() -> Generator[Session, None, None]:
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================
#   DB PING (health)
# ============================

def db_ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning(f"[DB] ping falló: {exc}")
        return False


# ============================
#   SEED SUPERADMIN (OBLIGATORIO)
# ============================

def seed_superadmin() -> None:
    """
    Crea el superadmin dueño de Orbion si no existe.

    ✔ Corre en TODOS los entornos
    ✔ Idempotente (no duplica)
    ✔ Respeta constraint: superadmin => negocio_id NULL
    """
    from core.models import Usuario, Negocio
    from core.security import hash_password

    email_root = settings.SUPERADMIN_EMAIL
    password_root = settings.SUPERADMIN_PASSWORD
    negocio_nombre = settings.SUPERADMIN_BUSINESS_NAME

    if not email_root or not password_root:
        logger.error(
            "[SEED_SUPERADMIN] SUPERADMIN_EMAIL o SUPERADMIN_PASSWORD no definidos. "
            "El sistema requiere un superadmin operativo."
        )
        return

    if settings.APP_ENV == "production" and password_root in {
        "changeme",
        "12345678",
        "admin",
        "password",
    }:
        logger.warning(
            "[SEED_SUPERADMIN] SUPERADMIN_PASSWORD es débil en producción. "
            "Esto es riesgoso. Cambiar inmediatamente."
        )

    db: Session = SessionLocal()
    try:
        # 1) Si el superadmin ya existe, no hacemos nada
        existing = db.query(Usuario).filter(Usuario.email == email_root).first()
        if existing:
            logger.info(f"[SEED_SUPERADMIN] Superadmin ya existe: {email_root}")
            return

        # 2) (Opcional) Asegurar negocio "Global" (para consola/impersonación/reportes)
        #    OJO: el superadmin NO debe tener negocio_id, pero el negocio puede existir igual.
        if negocio_nombre:
            negocio_global = (
                db.query(Negocio)
                .filter(Negocio.nombre_fantasia == negocio_nombre)
                .first()
            )
            if not negocio_global:
                negocio_global = Negocio(
                    nombre_fantasia=negocio_nombre,
                    whatsapp_notificaciones=None,
                    estado="activo",
                    plan_tipo="demo",
                )
                db.add(negocio_global)
                db.flush()

        # 3) Crear superadmin (✅ negocio_id NULL por constraint)
        superadmin = Usuario(
            negocio_id=None,  # ✅ CLAVE: superadmin global no pertenece a un negocio
            email=email_root,
            password_hash=hash_password(password_root),
            rol="superadmin",
            activo=1,
            nombre_mostrado="Superadmin Orbion",
        )

        db.add(superadmin)
        db.commit()

        logger.info(
            f"[SEED_SUPERADMIN] Superadmin creado: {email_root} (negocio seed='{negocio_nombre}')"
        )

    except Exception as exc:
        db.rollback()
        logger.exception(f"[SEED_SUPERADMIN] Error creando superadmin: {exc}")
    finally:
        db.close()


# ============================
#   INIT DB
# ============================

def init_db() -> None:
    """
    Inicialización de base de datos.

    ✔ SQLite: create_all()
    ✔ Dev/Staging: create_all() (hasta migrar a Alembic)
    ✔ Producción: NO create_all(), solo seed
    """
    import core.models  # noqa: F401

    env = (settings.APP_ENV or "development").lower()

    if _is_sqlite(settings.DATABASE_URL):
        logger.info("[INIT_DB] SQLite detectado -> create_all().")
        Base.metadata.create_all(bind=engine)

    elif env in {"development", "staging"}:
        logger.info("[INIT_DB] DB externa en dev/staging -> create_all() temporal.")
        Base.metadata.create_all(bind=engine)

    else:
        logger.info("[INIT_DB] Producción -> sin create_all() (migraciones).")

    logger.info("[INIT_DB] Ejecutando seed_superadmin()...")
    seed_superadmin()

    logger.info("[INIT_DB] Inicialización de BD completada.")
