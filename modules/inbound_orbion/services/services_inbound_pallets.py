# modules/inbound_orbion/services/services_inbound_pallets.py
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import InboundLinea, InboundPallet, InboundPalletItem
from core.models.enums import PalletEstado
from core.models.time import utcnow

from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_config_inbound,
    validar_recepcion_editable,
)

# ============================
# Helpers
# ============================

def _calcular_peso_neto(peso_bruto_kg: float | None, peso_tara_kg: float | None) -> float | None:
    if peso_bruto_kg is None or peso_tara_kg is None:
        return None
    return round(float(peso_bruto_kg) - float(peso_tara_kg), 3)

def _to_positive_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
        v = v.replace(",", ".")
    try:
        n = float(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor numérico inválido. Usa números (ej: 10 o 10.5).") from exc
    return n if n > 0 else None

def _assert_pallet_editable(pallet: InboundPallet) -> None:
    if pallet.estado in (PalletEstado.LISTO, PalletEstado.BLOQUEADO):
        raise InboundDomainError("Este pallet no se puede modificar porque ya está LISTO o BLOQUEADO.")

def _sum_asignado_por_linea_en_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    linea_id: int,
) -> tuple[float, float]:
    row = (
        db.query(
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPalletItem.linea_id == linea_id,
        )
        .first()
    )
    return float(row[0] or 0), float(row[1] or 0)

def _get_linea_cantidad_base(linea: InboundLinea) -> float | None:
    v = getattr(linea, "cantidad_recibida", None)
    if v is None:
        v = getattr(linea, "cantidad_esperada", None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _get_linea_kg_base(linea: InboundLinea) -> float | None:
    for attr in ("kilos", "peso_kg", "peso_total_kg"):
        if hasattr(linea, attr):
            v = getattr(linea, attr)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
    return None

# ============================
# Crear pallet
# ============================

def crear_pallet_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    codigo_pallet: str,
    peso_bruto_kg: float | None = None,
    peso_tara_kg: float | None = None,
    bultos: int | None = None,
    temperatura_promedio: float | None = None,
    observaciones: str | None = None,
    creado_por_id: int | None = None,
) -> InboundPallet:
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    codigo_norm = (codigo_pallet or "").strip().upper()
    if not codigo_norm:
        raise InboundDomainError("El código del pallet es obligatorio.")

    dup = (
        db.query(InboundPallet.id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion.id,
            InboundPallet.codigo_pallet == codigo_norm,
        )
        .first()
    )
    if dup:
        raise InboundDomainError(f"El pallet con código '{codigo_norm}' ya existe en esta recepción.")

    pallet = InboundPallet(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        codigo_pallet=codigo_norm,
        estado=PalletEstado.ABIERTO,
        peso_bruto_kg=peso_bruto_kg,
        peso_tara_kg=peso_tara_kg,
        peso_neto_kg=_calcular_peso_neto(peso_bruto_kg, peso_tara_kg),
        bultos=bultos,
        temperatura_promedio=temperatura_promedio,
        observaciones=(observaciones or "").strip() or None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    db.add(pallet)
    db.commit()
    db.refresh(pallet)
    return pallet

# ============================
# Agregar item
# ============================

def agregar_items_a_pallet(
    db: Session,
    negocio_id: int,
    pallet_id: int,
    items: Iterable[dict[str, Any]],
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, pallet.recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    try:
        for item_data in items:
            linea_id_raw = item_data.get("linea_id")
            if linea_id_raw is None:
                raise InboundDomainError("Cada ítem debe incluir 'linea_id'.")

            try:
                linea_id = int(linea_id_raw)
            except (TypeError, ValueError) as exc:
                raise InboundDomainError("Debes seleccionar una línea válida.") from exc

            linea = db.get(InboundLinea, linea_id)
            if not linea or linea.negocio_id != negocio_id:
                raise InboundDomainError("Línea inbound no encontrada para este negocio.")
            if linea.recepcion_id != recepcion.id:
                raise InboundDomainError("La línea seleccionada no pertenece a esta recepción.")

            cantidad = _to_positive_float(item_data.get("cantidad"))
            peso_kg = _to_positive_float(item_data.get("peso_kg"))

            if cantidad is None and peso_kg is None:
                raise InboundDomainError("Debes ingresar una cantidad o un peso mayor a cero.")

            # No duplicar línea dentro del mismo pallet
            if (
                db.query(InboundPalletItem.id)
                .filter(InboundPalletItem.pallet_id == pallet.id, InboundPalletItem.linea_id == linea.id)
                .first()
            ):
                raise InboundDomainError("Esta línea ya está asignada a este pallet.")

            cant_base = _get_linea_cantidad_base(linea)
            kg_base = _get_linea_kg_base(linea)

            cant_asig, kg_asig = _sum_asignado_por_linea_en_recepcion(
                db,
                negocio_id=negocio_id,
                recepcion_id=recepcion.id,
                linea_id=linea.id,
            )

            if cantidad is not None:
                if cant_base is None:
                    raise InboundDomainError("Esta línea no tiene cantidad base para asignar.")
                pend = float(cant_base) - float(cant_asig)
                if cantidad > pend + 1e-9:
                    raise InboundDomainError(f"Cantidad supera el pendiente. Pendiente: {max(pend, 0):.3f}")

            if peso_kg is not None:
                if kg_base is None:
                    raise InboundDomainError("Esta línea no tiene kilos base para asignar (completa kilos en la línea).")
                pend = float(kg_base) - float(kg_asig)
                if peso_kg > pend + 1e-9:
                    raise InboundDomainError(f"Peso supera el pendiente. Pendiente: {max(pend, 0):.3f} kg")

            item = InboundPalletItem(
                negocio_id=negocio_id,
                pallet_id=pallet.id,
                linea_id=linea.id,
                cantidad=cantidad,
                peso_kg=peso_kg,
                created_at=utcnow(),
            )
            db.add(item)

            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise InboundDomainError("Esta línea ya está asignada a este pallet.") from exc

        pallet.updated_at = utcnow()
        db.commit()

    except InboundDomainError:
        db.rollback()
        raise

# ============================
# Quitar item
# ============================

def quitar_item_de_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    pallet_item_id: int,
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id or pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("Pallet inbound no encontrado para esta recepción.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    item = db.get(InboundPalletItem, pallet_item_id)
    if not item or item.pallet_id != pallet.id:
        raise InboundDomainError("Ítem no encontrado para este pallet.")

    db.delete(item)
    pallet.updated_at = utcnow()
    db.commit()

# ============================
# Eliminar pallet
# ============================

def eliminar_pallet_inbound(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id or pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("Pallet inbound no encontrado para esta recepción.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    db.query(InboundPalletItem).filter(InboundPalletItem.pallet_id == pallet.id).delete()
    db.delete(pallet)
    db.commit()

# ============================
# Cerrar / Reabrir
# ============================

def marcar_pallet_listo(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int, user_id: int) -> None:
    pallet = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.id == pallet_id,
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .first()
    )
    if not pallet:
        raise InboundDomainError("Pallet no encontrado.")

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    if not (pallet.observaciones or "").strip():
        raise InboundDomainError("Para cerrar el pallet debes ingresar observaciones (mínimo algo descriptivo).")

    tiene_items = db.query(InboundPalletItem.id).filter(InboundPalletItem.pallet_id == pallet.id).first()
    if not tiene_items:
        raise InboundDomainError("No puedes cerrar un pallet sin líneas asignadas.")

    pallet.estado = PalletEstado.LISTO
    pallet.cerrado_por_id = user_id
    pallet.cerrado_at = utcnow()
    pallet.updated_at = utcnow()
    db.commit()

def reabrir_pallet(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int) -> None:
    pallet = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.id == pallet_id,
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .first()
    )
    if not pallet:
        raise InboundDomainError("Pallet no encontrado.")

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    pallet.estado = PalletEstado.ABIERTO
    pallet.cerrado_por_id = None
    pallet.cerrado_at = None
    pallet.updated_at = utcnow()
    db.commit()
