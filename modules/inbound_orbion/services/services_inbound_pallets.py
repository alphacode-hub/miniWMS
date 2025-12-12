# modules/inbound_orbion/services/services_inbound_pallets.py

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from core.models import InboundPallet, InboundPalletItem, InboundLinea
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)


def _calcular_peso_neto(peso_bruto_kg: float | None, peso_tara_kg: float | None) -> float | None:
    if peso_bruto_kg is None or peso_tara_kg is None:
        return None
    return peso_bruto_kg - peso_tara_kg


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

    if not codigo_pallet or not codigo_pallet.strip():
        raise InboundDomainError("El código del pallet es obligatorio.")

    pallet = InboundPallet(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        codigo_pallet=codigo_pallet.strip().upper(),
        peso_bruto_kg=peso_bruto_kg,
        peso_tara_kg=peso_tara_kg,
        peso_neto_kg=_calcular_peso_neto(peso_bruto_kg, peso_tara_kg),
        bultos=bultos,
        temperatura_promedio=temperatura_promedio,
        observaciones=(observaciones or "").strip() or None,
        creado_en=datetime.utcnow(),
        creado_por_id=creado_por_id,
    )

    db.add(pallet)
    db.commit()
    db.refresh(pallet)
    return pallet


def agregar_items_a_pallet(
    db: Session,
    negocio_id: int,
    pallet_id: int,
    items: Iterable[dict],
) -> None:
    """
    items: iterable de dicts:
        { "linea_id": int, "cantidad": float | None, "peso_kg": float | None }
    """
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")

    recepcion = obtener_recepcion_segura(db, pallet.recepcion_id, negocio_id)

    for item_data in items:
        linea_id = int(item_data.get("linea_id"))
        cantidad = item_data.get("cantidad")
        peso_kg = item_data.get("peso_kg")

        linea = db.get(InboundLinea, linea_id)
        if not linea:
            raise InboundDomainError(f"Línea inbound {linea_id} no encontrada.")
        if linea.recepcion_id != recepcion.id:
            raise InboundDomainError(
                f"La línea {linea_id} no pertenece a la recepción del pallet."
            )

        item = InboundPalletItem(
            pallet_id=pallet.id,
            linea_id=linea.id,
            cantidad=cantidad,
            peso_kg=peso_kg,
        )
        db.add(item)

    db.commit()


def eliminar_pallet_inbound(
    db: Session,
    negocio_id: int,
    pallet_id: int,
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")

    db.delete(pallet)
    db.commit()
