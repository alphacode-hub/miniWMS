# core/bootstrap.py
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.logging_config import logger
from core.security import hash_password
from core.models import Negocio, Usuario, TenantType
from core.models.enums import NegocioEstado


def ensure_global_negocio(db: Session, *, negocio_nombre: str) -> Negocio:
    """
    Asegura el negocio global interno (SYSTEM) usado por ORBION para seed/ops.

    Reglas:
    - Este negocio NO es un cliente (no debe ser CUSTOMER).
    - El superadmin global NO se asocia a negocio (negocio_id NULL) por constraint.
    - Idempotente: si existe, lo repara a SYSTEM si estuviera mal.
    """
    nombre = (negocio_nombre or "").strip() or "ORBION"

    n = (
        db.query(Negocio)
        .filter(Negocio.nombre_fantasia.isnot(None))
        .filter(func.lower(Negocio.nombre_fantasia) == nombre.lower())
        .first()
    )

    if n:
        changed = False

        # Reparar tenant_type => SYSTEM
        try:
            if getattr(n, "tenant_type", None) != TenantType.SYSTEM:
                n.tenant_type = TenantType.SYSTEM
                changed = True
        except Exception:
            # si por alguna razón falla, lo dejamos logueado
            logger.warning("[BOOTSTRAP] no se pudo validar tenant_type en negocio_global id=%s", getattr(n, "id", None))

        # Reparar estado => ACTIVO (Enum)
        try:
            if getattr(n, "estado", None) != NegocioEstado.ACTIVO:
                n.estado = NegocioEstado.ACTIVO
                changed = True
        except Exception:
            pass

        # Aplicar defaults system (entitlements + tenant_type)
        try:
            if hasattr(n, "set_system_defaults"):
                n.set_system_defaults()
                changed = True
        except Exception:
            logger.warning("[BOOTSTRAP] set_system_defaults falló en negocio_global id=%s", getattr(n, "id", None))

        if changed:
            db.commit()
            db.refresh(n)
            logger.info("[BOOTSTRAP] negocio_global reparado a SYSTEM id=%s nombre=%s", n.id, n.nombre_fantasia)
        else:
            logger.info("[BOOTSTRAP] negocio_global ya OK (SYSTEM) id=%s nombre=%s", n.id, n.nombre_fantasia)

        return n

    # Crear nuevo negocio global (SYSTEM)
    n = Negocio(
        nombre_fantasia=nombre,
        tenant_type=TenantType.SYSTEM,
        estado=NegocioEstado.ACTIVO,
        plan_tipo="legacy",
    )

    # Entitlements y defaults system (idempotente)
    if hasattr(n, "set_system_defaults"):
        try:
            n.set_system_defaults()
        except Exception:
            logger.warning("[BOOTSTRAP] set_system_defaults falló al crear negocio_global nombre=%s", nombre)

    db.add(n)
    db.flush()  # genera ID sin cerrar transacción
    db.commit()
    db.refresh(n)

    logger.info("[BOOTSTRAP] negocio_global creado SYSTEM id=%s nombre=%s", n.id, n.nombre_fantasia)
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

    Además:
    - asegura el negocio global interno (SYSTEM) con ensure_global_negocio().
    """
    email_norm = (email or "").strip().lower()
    if not email_norm or not password:
        logger.warning("[BOOTSTRAP] superadmin no creado: email/password vacíos")
        return

    db = SessionLocal()
    try:
        # negocio global interno ORBION (SYSTEM)
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
