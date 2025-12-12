# modules/inbound_orbion/services/services_inbound_citas.py

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.models import InboundCita, InboundRecepcion
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)


# ============================
#   HELPERS
# ============================

def _validar_estado_cita(estado: str) -> None:
    estados_validos = {
        "PROGRAMADA",
        "ARRIBADO",
        "RETRASADO",
        "CANCELADA",
        "COMPLETADA",
    }
    if estado not in estados_validos:
        raise InboundDomainError(
            f"Estado de cita inválido: {estado}. "
            f"Debe ser uno de {', '.join(sorted(estados_validos))}."
        )


# ============================
#   CRUD BÁSICO CITAS
# ============================

def crear_cita_inbound(
    db: Session,
    negocio_id: int,
    proveedor: str | None,
    transportista: str | None,
    patente_camion: str | None,
    nombre_conductor: str | None,
    fecha_hora_cita: datetime,
    observaciones: str | None = None,
) -> InboundCita:
    if fecha_hora_cita is None:
        raise InboundDomainError("La fecha y hora de la cita es obligatoria.")

    cita = InboundCita(
        negocio_id=negocio_id,
        proveedor=(proveedor or "").strip() or None,
        transportista=(transportista or "").strip() or None,
        patente_camion=(patente_camion or "").strip().upper() or None,
        nombre_conductor=(nombre_conductor or "").strip() or None,
        fecha_hora_cita=fecha_hora_cita,
        estado="PROGRAMADA",
        observaciones=(observaciones or "").strip() or None,
    )

    db.add(cita)
    db.commit()
    db.refresh(cita)
    return cita


def actualizar_cita_inbound(
    db: Session,
    negocio_id: int,
    cita_id: int,
    **updates: Any,
) -> InboundCita:
    cita = db.get(InboundCita, cita_id)
    if not cita or cita.negocio_id != negocio_id:
        raise InboundDomainError("Cita inbound no encontrada para este negocio.")

    if "estado" in updates and updates["estado"] is not None:
        _validar_estado_cita(str(updates["estado"]))

    for field, value in updates.items():
        if not hasattr(cita, field):
            continue
        if field in ("proveedor", "transportista", "nombre_conductor"):
            value = (value or "").strip() or None
        if field == "patente_camion":
            value = (value or "").strip().upper() or None

        setattr(cita, field, value)

    db.commit()
    db.refresh(cita)
    return cita


def marcar_llegada_cita(
    db: Session,
    negocio_id: int,
    cita_id: int,
    fecha_llegada: datetime | None = None,
) -> InboundCita:
    cita = db.get(InboundCita, cita_id)
    if not cita or cita.negocio_id != negocio_id:
        raise InboundDomainError("Cita inbound no encontrada para este negocio.")

    cita.fecha_hora_llegada_real = fecha_llegada or datetime.utcnow()
    # Si estaba programada → pasa a ARRIBADO
    if cita.estado == "PROGRAMADA":
        cita.estado = "ARRIBADO"

    db.commit()
    db.refresh(cita)
    return cita


def vincular_cita_a_recepcion(
    db: Session,
    negocio_id: int,
    cita_id: int,
    recepcion_id: int,
) -> InboundCita:
    cita = db.get(InboundCita, cita_id)
    if not cita or cita.negocio_id != negocio_id:
        raise InboundDomainError("Cita inbound no encontrada para este negocio.")

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    cita.recepcion_id = recepcion.id
    recepcion.cita_id = cita.id

    db.commit()
    db.refresh(cita)
    return cita
