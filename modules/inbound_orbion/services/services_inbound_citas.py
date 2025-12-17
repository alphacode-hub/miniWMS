# modules/inbound_orbion/services/services_inbound_citas.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.models.inbound.citas import InboundCita
from core.models.inbound.proveedores import Proveedor
from core.models.enums import CitaEstado

from .services_inbound_core import InboundDomainError


# ==========================================================
# Helpers
# ==========================================================

def _get_proveedor_opcional(
    db: Session,
    negocio_id: int,
    proveedor_id: Optional[int],
) -> Optional[Proveedor]:
    if not proveedor_id:
        return None

    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or proveedor.negocio_id != negocio_id:
        raise InboundDomainError("Proveedor no válido para este negocio.")
    return proveedor


def obtener_cita_segura(
    db: Session,
    negocio_id: int,
    cita_id: int,
) -> InboundCita:
    cita = db.get(InboundCita, cita_id)
    if not cita or cita.negocio_id != negocio_id:
        raise InboundDomainError("Cita no encontrada para este negocio.")
    return cita


# ==========================================================
# LISTAR
# ==========================================================

def listar_citas(
    db: Session,
    negocio_id: int,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    estado: Optional[CitaEstado] = None,
    limit: int = 100,
) -> List[InboundCita]:
    q = db.query(InboundCita).filter(
        InboundCita.negocio_id == negocio_id
    )

    if desde:
        q = q.filter(InboundCita.fecha_programada >= desde)
    if hasta:
        q = q.filter(InboundCita.fecha_programada <= hasta)
    if estado:
        q = q.filter(InboundCita.estado == estado)

    return (
        q.order_by(InboundCita.fecha_programada.asc())
        .limit(limit)
        .all()
    )


# ==========================================================
# CREAR
# ==========================================================

def crear_cita(
    db: Session,
    negocio_id: int,
    fecha_programada: datetime,
    proveedor_id: Optional[int] = None,
    referencia: Optional[str] = None,
    notas: Optional[str] = None,
) -> InboundCita:
    if not fecha_programada:
        raise InboundDomainError("La fecha programada es obligatoria.")

    _get_proveedor_opcional(db, negocio_id, proveedor_id)

    cita = InboundCita(
        negocio_id=negocio_id,
        proveedor_id=proveedor_id,
        fecha_programada=fecha_programada,
        referencia=(referencia or "").strip() or None,
        notas=(notas or "").strip() or None,
        estado=CitaEstado.PROGRAMADA,
    )

    try:
        db.add(cita)
        db.commit()
        db.refresh(cita)
        return cita
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear la cita.") from exc


# ==========================================================
# ACTUALIZAR
# ==========================================================

def actualizar_cita(
    db: Session,
    negocio_id: int,
    cita_id: int,
    *,
    fecha_programada: Optional[datetime] = None,
    proveedor_id: Optional[int] = None,
    referencia: Optional[str] = None,
    notas: Optional[str] = None,
    estado: Optional[CitaEstado] = None,
) -> InboundCita:
    cita = obtener_cita_segura(db, negocio_id, cita_id)

    if proveedor_id is not None:
        _get_proveedor_opcional(db, negocio_id, proveedor_id)
        cita.proveedor_id = proveedor_id

    if fecha_programada is not None:
        cita.fecha_programada = fecha_programada

    if referencia is not None:
        cita.referencia = referencia.strip() or None

    if notas is not None:
        cita.notas = notas.strip() or None

    if estado is not None:
        cita.estado = estado

    try:
        db.commit()
        db.refresh(cita)
        return cita
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar la cita.") from exc


# ==========================================================
# CAMBIAR ESTADO
# ==========================================================

def cambiar_estado_cita(
    db: Session,
    negocio_id: int,
    cita_id: int,
    nuevo_estado: CitaEstado,
) -> InboundCita:
    cita = obtener_cita_segura(db, negocio_id, cita_id)

    cita.estado = nuevo_estado

    db.commit()
    db.refresh(cita)
    return cita
