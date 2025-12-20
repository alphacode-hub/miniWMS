# core/services/services_audit.py
"""
Servicio de auditoría – ORBION (SaaS enterprise)

✅ Centraliza auditoría de acciones
✅ Seguro ante fallos (no rompe flujo principal)
✅ Serialización JSON consistente
✅ Enterprise-friendly:
   - No hace commit obligatorio (usa flush por defecto)
   - Soporta superadmin global vía negocio "Global" (si existe)
   - No genera loops de transacción (rollback solo si corresponde)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Auditoria, Negocio


# ============================
#   SERIALIZACIÓN
# ============================

def _serialize_detalle(detalle: Any) -> str:
    """
    Serializa el detalle de auditoría de forma segura.
    """
    try:
        if isinstance(detalle, (dict, list)):
            return json.dumps(detalle, ensure_ascii=False, default=str)
        return str(detalle)
    except Exception as exc:
        logger.warning(f"[AUDITORIA] Error serializando detalle: {exc}")
        return "<detalle_no_serializable>"


def _resolve_negocio_id(db: Session, user: dict) -> int | None:
    """
    Regla enterprise:
    - Si el usuario viene con negocio_id => usarlo
    - Si es superadmin global (sin negocio_id) => usar negocio "Global" si existe
      (Tu DB seed ya puede crearlo; si no existe, no auditamos para no romper)
    """
    negocio_id = user.get("negocio_id")
    if negocio_id:
        return int(negocio_id)

    rol_real = user.get("rol_real") or user.get("rol")
    if rol_real != "superadmin":
        return None

    try:
        negocio_nombre = (user.get("negocio") or "Global").strip() or "Global"
        ng = db.query(Negocio).filter(Negocio.nombre_fantasia == negocio_nombre).first()
        if not ng and negocio_nombre != "Global":
            ng = db.query(Negocio).filter(Negocio.nombre_fantasia == "Global").first()
        return int(ng.id) if ng else None
    except Exception:
        return None


# ============================
#   AUDITORÍA
# ============================

def registrar_auditoria(
    db: Session,
    user: dict,
    accion: str,
    detalle: Any | None = None,
    *,
    commit: bool = False,
) -> None:
    """
    Registra una acción en la tabla de auditoría.

    Enterprise:
    - No debe lanzar excepción hacia arriba
    - Por defecto NO hace commit (usa flush). El flujo llamador controla transacción.
    - Si commit=True, intenta commit autónomo (útil en endpoints simples).
    - Superadmin global: audita contra Negocio "Global" si existe.
    """
    try:
        negocio_id = _resolve_negocio_id(db, user)
        if not negocio_id:
            # No rompemos flujo: si no hay negocio_id (y no hay Global), omitimos auditoría
            return

        usuario_email = (user.get("email") or "sistema").strip() or "sistema"

        reg = Auditoria(
            negocio_id=negocio_id,
            usuario=usuario_email,
            accion=str(accion),
            detalle=_serialize_detalle(detalle),
        )
        db.add(reg)

        if commit:
            db.commit()
        else:
            # ✅ enterprise: no fragmentar transacciones del flujo principal
            db.flush()

    except Exception as exc:
        # Solo rollback si hay una transacción activa
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            pass

        logger.error(f"[AUDITORIA] No se pudo registrar acción '{accion}': {exc}")
