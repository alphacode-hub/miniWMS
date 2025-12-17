# modules/inbound_orbion/services/services_inbound_reconciliacion.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from core.models.inbound.lineas import InboundLinea
from core.models.inbound.pallets import InboundPalletItem, InboundPallet


def _r3(v: float | None) -> float | None:
    if v is None:
        return None
    return round(float(v), 3)

def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _diff(fisico: float | None, doc: float | None) -> float | None:
    if fisico is None or doc is None:
        return None
    return float(fisico) - float(doc)

def _clasificar_eje(doc: float | None, fisico: float | None, tol: float) -> str:
    if doc is None:
        return "SIN_DOC"
    if fisico is None:
        return "SIN_FISICO"
    if abs(fisico - doc) <= tol:
        return "OK"
    if fisico < doc:
        return "FALTANTE"
    return "SOBRANTE"

def _clasificar_linea(doc_qty: float | None, doc_kg: float | None, fis_qty: float, fis_kg: float, tol: float) -> str:
    if doc_qty is None and doc_kg is None:
        return "SIN_DOCUMENTO"

    estados: List[str] = []
    if doc_qty is not None:
        estados.append(_clasificar_eje(doc_qty, fis_qty, tol))
    if doc_kg is not None:
        estados.append(_clasificar_eje(doc_kg, fis_kg, tol))

    has_falt = any(s == "FALTANTE" for s in estados)
    has_sobr = any(s == "SOBRANTE" for s in estados)
    if has_falt and has_sobr:
        return "MIXTO"
    if has_falt:
        return "FALTANTE"
    if has_sobr:
        return "SOBRANTE"
    return "OK"


def reconciliar_recepcion(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    *,
    tol: float = 1e-6,
    include_lineas: bool = True,
    write_optional_fields: bool = True,
) -> Dict[str, Any]:

    lineas: List[InboundLinea] = (
        db.query(InboundLinea)
        .filter(
            InboundLinea.negocio_id == negocio_id,
            InboundLinea.recepcion_id == recepcion_id,
        )
        .order_by(InboundLinea.id.asc())
        .all()
    )

    if not lineas:
        return {
            "lineas_total": 0,
            "lineas_actualizadas": 0,
            "totales": {"fisico_cantidad": 0.0, "fisico_kg": 0.0},
            "resumen_estados": {},
            "lineas": [] if include_lineas else None,
        }

    linea_ids = [l.id for l in lineas]

    rows = (
        db.query(
            InboundPalletItem.linea_id.label("linea_id"),
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0.0).label("sum_cantidad"),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0.0).label("sum_peso_kg"),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPalletItem.negocio_id == negocio_id,
            InboundPalletItem.linea_id.in_(linea_ids),
        )
        .group_by(InboundPalletItem.linea_id)
        .all()
    )

    sum_por_linea: Dict[int, Tuple[float, float]] = {
        int(r.linea_id): (float(r.sum_cantidad or 0.0), float(r.sum_peso_kg or 0.0))
        for r in rows
    }

    total_fis_qty = 0.0
    total_fis_kg = 0.0
    actualizadas = 0
    resumen_estados: Dict[str, int] = {}
    lineas_out: List[Dict[str, Any]] = []

    for ln in lineas:
        fis_qty, fis_kg = sum_por_linea.get(int(ln.id), (0.0, 0.0))
        fis_qty = float(fis_qty or 0.0)
        fis_kg = float(fis_kg or 0.0)

        total_fis_qty += fis_qty
        total_fis_kg += fis_kg

        doc_qty = _to_float_or_none(getattr(ln, "cantidad_documento", None))
        doc_kg = _to_float_or_none(getattr(ln, "peso_kg", None))

        ln.cantidad_recibida = _r3(fis_qty)
        if hasattr(ln, "peso_recibido_kg"):
            setattr(ln, "peso_recibido_kg", _r3(fis_kg))

        d_qty = _diff(fis_qty, doc_qty)
        d_kg = _diff(fis_kg, doc_kg)

        estado_qty = _clasificar_eje(doc_qty, fis_qty, tol)
        estado_kg = _clasificar_eje(doc_kg, fis_kg, tol)
        estado_linea = _clasificar_linea(doc_qty, doc_kg, fis_qty, fis_kg, tol)

        resumen_estados[estado_linea] = resumen_estados.get(estado_linea, 0) + 1

        if write_optional_fields:
            if hasattr(ln, "estado_reconciliacion"):
                setattr(ln, "estado_reconciliacion", estado_linea)
            if hasattr(ln, "cantidad_diferencia"):
                setattr(ln, "cantidad_diferencia", _r3(d_qty) if d_qty is not None else None)
            if hasattr(ln, "peso_diferencia_kg"):
                setattr(ln, "peso_diferencia_kg", _r3(d_kg) if d_kg is not None else None)

        actualizadas += 1

        if include_lineas:
            lineas_out.append(
                {
                    "linea_id": ln.id,
                    "doc": {"cantidad": _r3(doc_qty), "kg": _r3(doc_kg)},
                    "fisico": {"cantidad": _r3(fis_qty), "kg": _r3(fis_kg)},
                    "diferencia": {
                        "cantidad": _r3(d_qty) if d_qty is not None else None,
                        "kg": _r3(d_kg) if d_kg is not None else None,
                    },
                    "estado": {"linea": estado_linea, "cantidad": estado_qty, "kg": estado_kg},
                }
            )

    db.commit()

    return {
        "lineas_total": len(lineas),
        "lineas_actualizadas": actualizadas,
        "totales": {"fisico_cantidad": _r3(total_fis_qty) or 0.0, "fisico_kg": _r3(total_fis_kg) or 0.0},
        "resumen_estados": resumen_estados,
        "lineas": lineas_out if include_lineas else None,
    }
