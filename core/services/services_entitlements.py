"""
services_entitlements.py – ORBION SaaS (enterprise)

Fuente única para el Hub:
- Estado por módulo (trial/active/past_due/cancelled/suspended)
- Período actual
- Límites por segmento + módulo
- Usage REAL calculado por período (sin depender de hooks)
- Incluye cancel_at_period_end para UX
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

from core.models import Negocio, Movimiento
from core.models.inbound import InboundRecepcion, InboundIncidencia
from core.models.enums import ModuleKey
from core.models.saas import SuscripcionModulo


# =========================================================
# LIMITES V1 (segmento + módulo)
# Ajusta valores a tu gusto.
# =========================================================

LIMITS_BY_SEGMENT: dict[str, dict[str, dict[str, float]]] = {
    "emprendedor": {
        "inbound": {"recepciones_mes": 200, "incidencias_mes": 1000},
        "wms": {"movimientos_mes": 5000, "productos": 3000},
    },
    "pyme": {
        "inbound": {"recepciones_mes": 2000, "incidencias_mes": 10000},
        "wms": {"movimientos_mes": 50000, "productos": 20000},
    },
    "enterprise": {
        "inbound": {"recepciones_mes": 100000, "incidencias_mes": 500000},
        "wms": {"movimientos_mes": 99999999, "productos": 99999999},
    },
}


# =========================================================
# HELPERS
# =========================================================

def _segmento_from_negocio(n: Negocio) -> str:
    """
    Segmento del negocio:
    - Si existe n.segmento -> lo usa
    - Si no existe -> "emprendedor"
    """
    seg = getattr(n, "segmento", None)
    seg = (seg or "emprendedor").strip().lower()
    if seg not in ("emprendedor", "pyme", "enterprise"):
        seg = "emprendedor"
    return seg


def _limits_for(seg: str, module_key: str) -> dict[str, float]:
    return (LIMITS_BY_SEGMENT.get(seg, {}) or {}).get(module_key, {}) or {}


def _period_dict(sub: SuscripcionModulo | None) -> dict[str, Any]:
    if not sub:
        return {"start": None, "end": None}
    return {
        "start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "end": sub.current_period_end.isoformat() if sub.current_period_end else None,
    }


def _enabled_from_status(status: str) -> bool:
    # enabled operativo (aunque tenga cancel programado)
    return status in ("trial", "active")


def _status_str(sub: SuscripcionModulo | None) -> str:
    if not sub:
        return "inactive"
    # status es Enum (SubscriptionStatus) en tu modelo
    try:
        return sub.status.value  # type: ignore[attr-defined]
    except Exception:
        return str(sub.status)


def _usage_inbound(
    db: Session,
    negocio_id: int,
    start,
    end,
) -> dict[str, float]:
    """
    Uso REAL Inbound por período:
    - recepciones_mes: TODAS las recepciones creadas en el período (no solo cerradas)
    - incidencias_mes: incidencias creadas en el período
    """
    usage: dict[str, float] = {"recepciones_mes": 0.0, "incidencias_mes": 0.0}
    if not start or not end:
        return usage

    receps = (
        db.query(InboundRecepcion)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .filter(InboundRecepcion.created_at >= start)
        .filter(InboundRecepcion.created_at < end)
        .count()
    )

    incs = (
        db.query(InboundIncidencia)
        .filter(InboundIncidencia.negocio_id == negocio_id)
        .filter(InboundIncidencia.created_at >= start)
        .filter(InboundIncidencia.created_at < end)
        .count()
    )

    usage["recepciones_mes"] = float(receps)
    usage["incidencias_mes"] = float(incs)
    return usage


def _usage_wms(
    db: Session,
    negocio_id: int,
    start,
    end,
) -> dict[str, float]:
    """
    Uso REAL WMS:
    - movimientos_mes: Movimientos en el período
    - productos: total productos (snapshot actual)
    """
    usage: dict[str, float] = {"movimientos_mes": 0.0, "productos": 0.0}

    if start and end:
        movs = (
            db.query(Movimiento)
            .filter(Movimiento.negocio_id == negocio_id)
            .filter(Movimiento.fecha >= start)
            .filter(Movimiento.fecha < end)
            .count()
        )
        usage["movimientos_mes"] = float(movs)

    # Productos: si existe modelo Producto, lo contamos; si no, 0.
    try:
        from core.models import Producto  # local import evita ciclos
        prods = db.query(Producto).filter(Producto.negocio_id == negocio_id).count()
        usage["productos"] = float(prods)
    except Exception:
        usage["productos"] = 0.0

    return usage


# =========================================================
# API PUBLICA
# =========================================================

def get_entitlements_snapshot(db: Session, negocio_id: int) -> Dict[str, Any]:
    """
    Snapshot que consume el HUB.
    Incluye: negocio + modules dict.
    Keys de modules: "inbound", "wms"
    """
    n = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not n:
        return {"negocio": {"id": negocio_id}, "modules": {}}

    seg = _segmento_from_negocio(n)

    subs = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .all()
    )

    modules: dict[str, Any] = {}

    for sub in subs:
        mk = sub.module_key.value if hasattr(sub.module_key, "value") else str(sub.module_key)
        status = _status_str(sub)
        enabled = _enabled_from_status(status)

        limits = _limits_for(seg, mk)
        period = _period_dict(sub)

        if mk == "inbound":
            usage = _usage_inbound(db, negocio_id, sub.current_period_start, sub.current_period_end)
        elif mk == "wms":
            usage = _usage_wms(db, negocio_id, sub.current_period_start, sub.current_period_end)
        else:
            usage = {}

        modules[mk] = {
            "enabled": enabled,
            "status": status,
            "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", 0)),
            "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
            "period": period,
            "limits": limits,
            "usage": usage,
        }

    return {
        "negocio": {
            "id": n.id,
            "nombre": n.nombre_fantasia,
            "segmento": seg,
        },
        "modules": modules,
    }
