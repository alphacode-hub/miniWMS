# core/services/services_audit.py
"""
Servicio de auditoría – ORBION (SaaS enterprise)

✔ Centraliza auditoría de acciones
✔ Seguro ante fallos (no rompe flujo principal)
✔ Serialización JSON consistente
✔ Preparado para crecer a:
  - auditoría por módulo
  - eventos de seguridad
  - exportación / analytics
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Auditoria


# ============================
#   AUDITORÍA
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


def registrar_auditoria(
    db: Session,
    user: dict,
    accion: str,
    detalle: Any | None = None,
) -> None:
    """
    Registra una acción en la tabla de auditoría.

    Reglas enterprise:
    - Nunca debe lanzar excepción hacia arriba
    - Siempre asociada a negocio + usuario efectivo
    """
    try:
        reg = Auditoria(
            negocio_id=user.get("negocio_id"),
            usuario=user.get("email", "sistema"),
            accion=accion,
            detalle=_serialize_detalle(detalle),
        )
        db.add(reg)
        db.commit()
    except Exception as exc:
        db.rollback()
        # No rompemos el flujo principal
        logger.error(f"[AUDITORIA] No se pudo registrar acción '{accion}': {exc}")
