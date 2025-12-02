# database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from config import settings
from logging_config import logger  # 👈 usamos el logger global

# ============================
# ENGINE, SESSION, BASE
# ============================

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,               # 👈 evita conexiones muertas en motores externos
    echo=settings.APP_DEBUG,          # 👈 solo logea SQL en modo debug
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    """
    Dependencia estándar de BD para usar en FastAPI:
    db: Session = Depends(get_db)
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================
# SEED SUPERADMIN
# ============================

def seed_superadmin():
    """
    Crea un usuario superadmin si no existe, asociado a un negocio global.
    OJO: imports locales para evitar ciclos.
    """
    from models import Usuario, Negocio
    from security import hash_password

    db: Session = SessionLocal()
    try:
        email_root = settings.SUPERADMIN_EMAIL
        password_root = settings.SUPERADMIN_PASSWORD
        negocio_nombre = settings.SUPERADMIN_BUSINESS_NAME

        if not email_root or not password_root:
            logger.warning(
                "[SEED_SUPERADMIN] SUPERADMIN_EMAIL o SUPERADMIN_PASSWORD no configurados. "
                "No se creará superadmin por defecto."
            )
            return

        # Aviso si en producción se dejó una clave insegura
        if settings.APP_ENV == "production" and password_root == "12345678":
            logger.warning(
                "[SEED_SUPERADMIN] SUPERADMIN_PASSWORD tiene un valor débil por defecto "
                "en entorno de producción. Cámbialo en el .env."
            )

        # ¿Ya existe el superadmin?
        existing = db.query(Usuario).filter(Usuario.email == email_root).first()
        if existing:
            logger.info(f"[SEED_SUPERADMIN] Superadmin ya existe: {email_root}")
            return

        # Buscar/crear negocio global
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
            )
            db.add(negocio_global)
            db.flush()  # para tener negocio_global.id

        superadmin = Usuario(
            negocio_id=negocio_global.id,
            email=email_root,
            password_hash=hash_password(password_root),
            rol="superadmin",
            activo=1,
            nombre_mostrado="Superadmin",
        )
        db.add(superadmin)
        db.commit()
        logger.info(f"[SEED_SUPERADMIN] Superadmin creado: {email_root} en negocio '{negocio_nombre}'")

    except Exception as e:
        db.rollback()
        logger.error(f"[SEED_SUPERADMIN] Error al crear superadmin: {e}")
    finally:
        db.close()


# ============================
# INIT_DB
# ============================

def init_db() -> None:
    """
    Crear tablas y ejecutar seed inicial.
    Llamar una vez al arrancar la app (evento startup).
    """
    # Import local para registrar modelos en Base.metadata
    import models  # noqa: F401

    logger.info("[INIT_DB] Creando tablas (si no existen)...")
    Base.metadata.create_all(bind=engine)
    logger.info("[INIT_DB] Tablas creadas/verificadas. Ejecutando seed_superadmin()...")
    seed_superadmin()
    logger.info("[INIT_DB] Proceso de inicialización de BD completado.")
