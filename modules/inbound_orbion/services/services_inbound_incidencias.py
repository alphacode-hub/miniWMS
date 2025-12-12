# modules/inbound_orbion/services/services_inbound_incidencias.py

from __future__ import annotations

from sqlalchemy.orm import Session

from core.models import InboundIncidencia
from .services_inbound_core import (
    InboundConfig,
    InboundDomainError,
    obtener_recepcion_segura,
    validar_recepcion_editable,
)


# ============================
#   INCIDENCIAS
# ============================

def crear_incidencia_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    tipo: str,
    criticidad: str,
    descripcion: str,
) -> InboundIncidencia:
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    if not tipo:
        raise InboundDomainError("El tipo de incidencia es obligatorio.")
    if criticidad not in ("baja", "media", "alta"):
        raise InboundDomainError("La criticidad debe ser baja, media o alta.")
    if not descripcion:
        raise InboundDomainError("La descripción de la incidencia es obligatoria.")

    incidencia = InboundIncidencia(
        recepcion_id=recepcion.id,
        tipo=tipo,
        criticidad=criticidad,
        descripcion=descripcion,
        # creado_por_id se setea desde la ruta si lo necesitas
    )
    db.add(incidencia)
    db.commit()
    db.refresh(incidencia)
    return incidencia


def eliminar_incidencia_inbound(
    db: Session,
    negocio_id: int,
    incidencia_id: int,
) -> None:
    incidencia = db.get(InboundIncidencia, incidencia_id)
    if not incidencia:
        raise InboundDomainError("Incidencia inbound no encontrada.")

    recepcion = obtener_recepcion_segura(db, incidencia.recepcion_id, negocio_id)
    # Config podría limitar borrado de incidencias en estados cerrados
    config = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, config)

    db.delete(incidencia)
    db.commit()
