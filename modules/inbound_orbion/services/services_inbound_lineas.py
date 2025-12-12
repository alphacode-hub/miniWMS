# modules/inbound_orbion/services/services_inbound_lineas.py

from __future__ import annotations

from datetime import datetime
from typing import Optional, Any

from sqlalchemy.orm import Session

from core.models import InboundLinea
from .services_inbound_core import (
    InboundConfig,
    InboundDomainError,
    obtener_recepcion_segura,
    validar_recepcion_editable,
    validar_producto_para_negocio,
)


# ============================
#   LÍNEAS DE RECEPCIÓN
# ============================

def crear_linea_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    producto_id: int,
    lote: Optional[str] = None,
    fecha_vencimiento: Optional[datetime] = None,
    cantidad_esperada: Optional[float] = None,
    cantidad_recibida: Optional[float] = None,
    unidad: Optional[str] = None,
    temperatura_objetivo: Optional[float] = None,
    temperatura_recibida: Optional[float] = None,
    observaciones: Optional[str] = None,
    peso_kg: Optional[float] = None,
    bultos: Optional[int] = None,
) -> InboundLinea:
    config = InboundConfig.from_negocio(db, negocio_id)
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    validar_recepcion_editable(recepcion, config)

    # Validar producto
    producto = validar_producto_para_negocio(db, producto_id, negocio_id)

    # Validaciones de negocio mínimas
    if config.require_lote and not lote:
        raise InboundDomainError("El lote es obligatorio para este negocio.")

    if config.require_fecha_vencimiento and not fecha_vencimiento:
        raise InboundDomainError("La fecha de vencimiento es obligatoria.")

    if config.require_temperatura and temperatura_recibida is None:
        raise InboundDomainError(
            "La temperatura recibida es obligatoria para este negocio."
        )

    linea = InboundLinea(
        recepcion_id=recepcion.id,
        producto_id=producto.id,
        lote=lote or None,
        fecha_vencimiento=fecha_vencimiento,
        cantidad_esperada=cantidad_esperada,
        cantidad_recibida=cantidad_recibida,
        unidad=unidad or producto.unidad,
        temperatura_objetivo=temperatura_objetivo,
        temperatura_recibida=temperatura_recibida,
        observaciones=observaciones or None,
        peso_kg=peso_kg,
        bultos=bultos,
    )

    db.add(linea)
    db.commit()
    db.refresh(linea)
    return linea


def actualizar_linea_inbound(
    db: Session,
    negocio_id: int,
    linea_id: int,
    **updates: Any,
) -> InboundLinea:
    linea = db.get(InboundLinea, linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")

    recepcion = obtener_recepcion_segura(db, linea.recepcion_id, negocio_id)
    config = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, config)

    # Si se cambia producto_id, validarlo
    producto_id = updates.get("producto_id")
    if producto_id is not None:
        producto = validar_producto_para_negocio(db, producto_id, negocio_id)
        linea.producto_id = producto.id

    for field, value in updates.items():
        if field == "producto_id":
            continue
        if hasattr(linea, field):
            setattr(linea, field, value)

    db.commit()
    db.refresh(linea)
    return linea


def eliminar_linea_inbound(
    db: Session,
    negocio_id: int,
    linea_id: int,
) -> None:
    linea = db.get(InboundLinea, linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")

    recepcion = obtener_recepcion_segura(db, linea.recepcion_id, negocio_id)
    config = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, config)

    db.delete(linea)
    db.commit()
