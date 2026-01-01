# modules/inbound_orbion/services/services_inbound_reconciliacion.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models.inbound.lineas import InboundLinea
from core.models.inbound.pallets import InboundPallet, InboundPalletItem
from core.models.enums import PalletEstado

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_config_inbound,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
)

# ✅ contrato oficial de línea (para saber si es CANTIDAD/PESO)
from modules.inbound_orbion.services.inbound_linea_contract import (
    InboundLineaContractError,
    InboundLineaModo,
    normalizar_linea,
)

_EPS = 1e-9


# =========================================================
# Helpers internos
# =========================================================

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


def _clasificar_linea(
    doc_qty: float | None,
    doc_kg: float | None,
    fis_qty: float | None,
    fis_kg: float | None,
    tol: float,
) -> str:
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


# =========================================================
# Conversión (kg/u) — usa override de línea o producto si existe
# =========================================================

def _resolver_peso_unitario_kg(linea: InboundLinea) -> float | None:
    v = getattr(linea, "peso_unitario_kg_override", None)
    if v is not None:
        try:
            n = float(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            pass

    prod = getattr(linea, "producto", None)
    if prod is not None:
        v2 = getattr(prod, "peso_unitario_kg", None)
        if v2 is not None:
            try:
                n2 = float(v2)
                return n2 if n2 > 0 else None
            except (TypeError, ValueError):
                pass

    return None


def _calc_kg_desde_cantidad(cant: float, kg_u: float) -> float:
    return round(float(cant) * float(kg_u), 3)


def _calc_cantidad_desde_kg(kg: float, kg_u: float) -> float:
    return round(float(kg) / float(kg_u), 3)


# =========================================================
# Fuente de verdad: pallets -> líneas
# =========================================================

def reconciliar_recepcion(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    *,
    tol: float = 1e-6,
    include_lineas: bool = True,
    write_optional_fields: bool = True,
    # - soft: ABIERTO + EN_PROCESO + LISTO (excluye BLOQUEADO)
    # - strict: solo LISTO (excluye BLOQUEADO)
    strict: bool = True,
    require_editable: bool = True,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Reconciliación enterprise:
    - Fuente de verdad: InboundPalletItem (REAL + ESTIMADOS)
    - Derivados en InboundLinea:
        - cantidad_recibida (siempre)
        - peso_recibido_kg (si existe)
    - CANTIDAD: kg físico se toma desde peso_estimado_kg (o deriva por kg/u si falta)
    - PESO: cantidad física se toma desde cantidad_estimada (o deriva por kg/u si falta)
    """

    # Guard de recepción (segura + editable si corresponde)
    if require_editable:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    else:
        _ = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    _cfg = obtener_config_inbound(db, negocio_id)

    # Estados de pallets que cuentan
    if strict:
        estados_ok = {PalletEstado.LISTO.value}
    else:
        estados_ok = {
            PalletEstado.ABIERTO.value,
            PalletEstado.EN_PROCESO.value,
            PalletEstado.LISTO.value,
        }
    estados_excluidos = {PalletEstado.BLOQUEADO.value}

    lineas: List[InboundLinea] = (
        db.query(InboundLinea)
        .filter(
            InboundLinea.negocio_id == negocio_id,
            InboundLinea.recepcion_id == recepcion_id,
            InboundLinea.activo == 1,
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
            "pallets_considerados": {"strict": strict, "estados": sorted(list(estados_ok))},
            "lineas": [] if include_lineas else None,
        }

    linea_ids = [int(l.id) for l in lineas]

    # ✅ SUMS: REAL + ESTIMADOS
    rows = (
        db.query(
            InboundPalletItem.linea_id.label("linea_id"),
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0.0).label("sum_cant_real"),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0.0).label("sum_kg_real"),
            func.coalesce(func.sum(InboundPalletItem.cantidad_estimada), 0.0).label("sum_cant_est"),
            func.coalesce(func.sum(InboundPalletItem.peso_estimado_kg), 0.0).label("sum_kg_est"),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPallet.estado.in_(list(estados_ok)),
            ~InboundPallet.estado.in_(list(estados_excluidos)),
            InboundPalletItem.negocio_id == negocio_id,
            InboundPalletItem.linea_id.in_(linea_ids),
        )
        .group_by(InboundPalletItem.linea_id)
        .all()
    )

    # linea_id -> (cant_real, kg_real, cant_est, kg_est)
    sum_por_linea: Dict[int, Tuple[float, float, float, float]] = {
        int(r.linea_id): (
            float(r.sum_cant_real or 0.0),
            float(r.sum_kg_real or 0.0),
            float(r.sum_cant_est or 0.0),
            float(r.sum_kg_est or 0.0),
        )
        for r in rows
    }

    total_fis_qty = 0.0
    total_fis_kg = 0.0
    actualizadas = 0
    resumen_estados: Dict[str, int] = {}
    lineas_out: List[Dict[str, Any]] = []

    for ln in lineas:
        cant_real, kg_real, cant_est, kg_est = sum_por_linea.get(int(ln.id), (0.0, 0.0, 0.0, 0.0))
        cant_real = float(cant_real or 0.0)
        kg_real = float(kg_real or 0.0)
        cant_est = float(cant_est or 0.0)
        kg_est = float(kg_est or 0.0)

        # Contrato oficial
        try:
            view = normalizar_linea(ln, allow_draft=False)
        except InboundLineaContractError as exc:
            raise InboundDomainError(f"Línea inválida según contrato: {str(exc)}") from exc

        kg_u = _resolver_peso_unitario_kg(ln)

        # ✅ físico operativo según modo
        fis_qty: float | None
        fis_kg: float | None

        if view.modo == InboundLineaModo.CANTIDAD:
            fis_qty = cant_real
            # kg: REAL si existe; sino ESTIMADO; sino deriva por kg/u
            if kg_real > _EPS:
                fis_kg = kg_real
            elif kg_est > _EPS:
                fis_kg = kg_est
            elif kg_u is not None and fis_qty > _EPS:
                fis_kg = _calc_kg_desde_cantidad(fis_qty, float(kg_u))
            else:
                fis_kg = None
        else:  # PESO
            fis_kg = kg_real
            # cantidad: REAL si existe; sino ESTIMADA; sino deriva por kg/u
            if cant_real > _EPS:
                fis_qty = cant_real
            elif cant_est > _EPS:
                fis_qty = cant_est
            elif kg_u is not None and fis_kg > _EPS:
                fis_qty = _calc_cantidad_desde_kg(fis_kg, float(kg_u))
            else:
                fis_qty = None

        total_fis_qty += float(fis_qty or 0.0)
        total_fis_kg += float(fis_kg or 0.0)

        doc_qty = _to_float_or_none(getattr(ln, "cantidad_documento", None))
        doc_kg = _to_float_or_none(getattr(ln, "peso_kg", None))

        # ✅ DERIVADOS (snapshot) — no editables por UI
        ln.cantidad_recibida = float(_r3(fis_qty) or 0.0)
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
                    "modo": view.modo.value if hasattr(view.modo, "value") else str(view.modo),
                    "doc": {"cantidad": _r3(doc_qty), "kg": _r3(doc_kg)},
                    "fisico": {"cantidad": _r3(fis_qty), "kg": _r3(fis_kg)},
                    "diferencia": {
                        "cantidad": _r3(d_qty) if d_qty is not None else None,
                        "kg": _r3(d_kg) if d_kg is not None else None,
                    },
                    "estado": {"linea": estado_linea, "cantidad": estado_qty, "kg": estado_kg},
                }
            )

    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "lineas_total": len(lineas),
        "lineas_actualizadas": actualizadas,
        "totales": {
            "fisico_cantidad": float(_r3(total_fis_qty) or 0.0),
            "fisico_kg": float(_r3(total_fis_kg) or 0.0),
        },
        "resumen_estados": resumen_estados,
        "pallets_considerados": {"strict": strict, "estados": sorted(list(estados_ok))},
        "lineas": lineas_out if include_lineas else None,
    }
