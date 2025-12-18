from __future__ import annotations

from typing import Optional, List
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select

from core.models.time import utcnow
from core.models.inbound.incidencias import InboundIncidencia
from core.models.inbound.recepciones import InboundRecepcion
from core.models.enums import IncidenciaEstado

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)


# ============================================================
# CREAR INCIDENCIA
# ============================================================

def crear_incidencia(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    tipo: str,
    criticidad: str,
    titulo: Optional[str],
    detalle: Optional[str],
    pallet_id: Optional[int] = None,
) -> InboundIncidencia:
    """
    Crea una incidencia inbound asociada a una recepción (y opcionalmente a un pallet).

    Reglas enterprise:
    - Multi-tenant estricto por negocio_id
    - Recepción debe existir
    - Estado inicial = CREADA
    """

    recepcion = obtener_recepcion_segura(
        db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
    )

    inc = InboundIncidencia(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        pallet_id=pallet_id,
        tipo=(tipo or "GENERAL").strip().upper(),
        criticidad=(criticidad or "MEDIA").strip().upper(),
        estado=IncidenciaEstado.CREADA.value,
        titulo=(titulo.strip() if titulo else None),
        detalle=(detalle.strip() if detalle else None),
        created_at=utcnow(),
    )

    db.add(inc)
    db.flush()

    return inc


# ============================================================
# LISTAR INCIDENCIAS POR RECEPCIÓN
# ============================================================

def listar_incidencias_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> List[InboundIncidencia]:
    """
    Retorna todas las incidencias asociadas a una recepción.

    - Ordenadas por fecha desc (más reciente primero)
    - Multi-tenant safe
    """

    obtener_recepcion_segura(
        db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
    )

    stmt = (
        select(InboundIncidencia)
        .where(InboundIncidencia.negocio_id == negocio_id)
        .where(InboundIncidencia.recepcion_id == recepcion_id)
        .order_by(InboundIncidencia.created_at.desc())
    )

    return list(db.execute(stmt).scalars().all())


# ============================================================
# OBTENER INCIDENCIA SEGURA
# ============================================================

def obtener_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
) -> InboundIncidencia:
    """
    Obtiene una incidencia asegurando pertenencia al negocio.
    """

    stmt = (
        select(InboundIncidencia)
        .where(InboundIncidencia.id == incidencia_id)
        .where(InboundIncidencia.negocio_id == negocio_id)
    )

    inc = db.execute(stmt).scalar_one_or_none()
    if not inc:
        raise InboundDomainError("Incidencia no encontrada.")

    return inc


# ============================================================
# CAMBIOS DE ESTADO
# ============================================================

def cerrar_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
) -> InboundIncidencia:
    """
    Marca una incidencia como CERRADA.

    Reglas:
    - Solo si no está ya cerrada
    """

    inc = obtener_incidencia(
        db,
        negocio_id=negocio_id,
        incidencia_id=incidencia_id,
    )

    if inc.estado == IncidenciaEstado.CERRADA.value:
        raise InboundDomainError("La incidencia ya está cerrada.")

    inc.estado = IncidenciaEstado.CERRADA.value
    db.flush()

    return inc


def reabrir_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
) -> InboundIncidencia:
    """
    Reabre una incidencia cerrada.
    """

    inc = obtener_incidencia(
        db,
        negocio_id=negocio_id,
        incidencia_id=incidencia_id,
    )

    if inc.estado != IncidenciaEstado.CERRADA.value:
        raise InboundDomainError("Solo se pueden reabrir incidencias cerradas.")

    inc.estado = IncidenciaEstado.CREADA.value
    db.flush()

    return inc


# ============================================================
# MÉTRICAS BÁSICAS (PARA DASHBOARD / RECEPCIÓN)
# ============================================================

def obtener_resumen_incidencias(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> dict:
    """
    Retorna métricas simples de incidencias para una recepción.
    """

    obtener_recepcion_segura(
        db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
    )

    stmt = (
        select(InboundIncidencia)
        .where(InboundIncidencia.negocio_id == negocio_id)
        .where(InboundIncidencia.recepcion_id == recepcion_id)
    )

    rows = list(db.execute(stmt).scalars().all())

    total = len(rows)
    abiertas = sum(1 for r in rows if r.estado != IncidenciaEstado.CERRADA.value)
    cerradas = total - abiertas
    criticas = sum(1 for r in rows if r.criticidad == "ALTA")

    return {
        "total": total,
        "abiertas": abiertas,
        "cerradas": cerradas,
        "criticas": criticas,
    }
