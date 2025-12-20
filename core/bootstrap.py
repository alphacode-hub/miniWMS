# core/bootstrap.py
from __future__ import annotations

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.database import SessionLocal
from core.security import hash_password
from core.models import Negocio, Usuario


def ensure_global_negocio(db: Session, *, negocio_nombre: str) -> Negocio:
    """
    Crea (si no existe) el negocio global (útil para datos/entitlements/seed),
    pero OJO: el superadmin global NO se asocia a un negocio (negocio_id NULL)
    por constraint ck_usuario_negocio_por_rol.
    """
    nombre = (negocio_nombre or "").strip() or "ORBION_GLOBAL"

    n = db.query(Negocio).filter(Negocio.nombre_fantasia == nombre).first()
    if n:
        return n

    n = Negocio(
        nombre_fantasia=nombre,
        estado="activo",
        plan_tipo="legacy",
    )
    db.add(n)
    db.flush()
    logger.info("[BOOTSTRAP] negocio_global creado id=%s nombre=%s", n.id, nombre)
    return n


def ensure_superadmin(
    *,
    email: str,
    password: str,
    negocio_nombre: str,
    user_display_name: str = "Superadmin",
) -> None:
    """
    Crea (si no existe) el superadmin global.
    Regla CRÍTICA por constraint ck_usuario_negocio_por_rol:
      - superadmin => negocio_id = NULL
    """
    email_norm = (email or "").strip().lower()
    if not email_norm or not password:
        logger.warning("[BOOTSTRAP] superadmin no creado: email/password vacíos")
        return

    db = SessionLocal()
    try:
        # (opcional) negocio global para seed / futuras referencias
        ensure_global_negocio(db, negocio_nombre=negocio_nombre)

        u = db.query(Usuario).filter(Usuario.email == email_norm).first()
        if u:
            changed = False

            # Rol
            if str(getattr(u, "rol", "")).lower() != "superadmin":
                u.rol = "superadmin"
                changed = True

            # Activo
            if int(getattr(u, "activo", 0) or 0) != 1:
                u.activo = 1
                changed = True

            # ✅ Superadmin debe quedar sin negocio
            if getattr(u, "negocio_id", None) is not None:
                u.negocio_id = None
                changed = True

            # nombre mostrado
            if hasattr(u, "nombre_mostrado"):
                nm = (getattr(u, "nombre_mostrado", None) or "").strip()
                if not nm:
                    u.nombre_mostrado = user_display_name
                    changed = True

            if changed:
                db.commit()
                logger.info("[BOOTSTRAP] superadmin existente normalizado email=%s", email_norm)
            else:
                logger.info("[BOOTSTRAP] superadmin ya existe email=%s", email_norm)
            return

        # Crear nuevo superadmin (negocio_id NULL)
        u = Usuario(
            negocio_id=None,  # ✅ clave
            email=email_norm,
            password_hash=hash_password(password),
            rol="superadmin",
            activo=1,
        )
        if hasattr(u, "nombre_mostrado"):
            u.nombre_mostrado = user_display_name

        db.add(u)

        try:
            db.flush()
        except Exception as exc:
            db.rollback()
            logger.exception("[BOOTSTRAP] flush superadmin falló: %s", exc)
            return

        db.commit()
        logger.info("[BOOTSTRAP] superadmin creado email=%s", email_norm)

    except Exception as exc:
        db.rollback()
        logger.exception("[BOOTSTRAP] error creando superadmin: %s", exc)
    finally:
        db.close()
