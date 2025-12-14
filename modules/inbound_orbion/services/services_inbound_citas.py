# modules/inbound_orbion/services/services_inbound_citas.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy.orm import Session

from core.models.inbound import InboundCita, InboundRecepcion
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

# ============================
#   CONSTANTES (DOMINIO)
# ============================

ESTADO_PROGRAMADA: Final[str] = "PROGRAMADA"
ESTADO_ARRIBADO: Final[str] = "ARRIBADO"
ESTADO_RETRASADO: Final[str] = "RETRASADO"
ESTADO_CANCELADA: Final[str] = "CANCELADA"
ESTADO_COMPLETADA: Final[str] = "COMPLETADA"

ESTADOS_CITA: Final[set[str]] = {
    ESTADO_PROGRAMADA,
    ESTADO_ARRIBADO,
    ESTADO_RETRASADO,
    ESTADO_CANCELADA,
    ESTADO_COMPLETADA,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================
#   HELPERS
# ============================

def _normalizar_estado(estado: str | None) -> str:
    est = (estado or "").strip().upper()
    return est


def _validar_estado_cita(estado: str | None) -> str:
    est = _normalizar_estado(estado)
    if not est:
        raise InboundDomainError("El estado de la cita es obligatorio.")
    if est not in ESTADOS_CITA:
        raise InboundDomainError(
            f"Estado de cita inválido: {est}. Debe ser uno de {', '.join(sorted(ESTADOS_CITA))}."
        )
    return est


def _norm_text(v: Any) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s or None


def _norm_patente(v: Any) -> str | None:
    s = _norm_text(v)
    return s.upper() if s else None


# ============================
#   CRUD CITAS
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
        proveedor=_norm_text(proveedor),
        transportista=_norm_text(transportista),
        patente_camion=_norm_patente(patente_camion),
        nombre_conductor=_norm_text(nombre_conductor),
        fecha_hora_cita=fecha_hora_cita,
        estado=ESTADO_PROGRAMADA,
        observaciones=_norm_text(observaciones),
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

    # Validar estado si viene
    if "estado" in updates and updates["estado"] is not None:
        cita.estado = _validar_estado_cita(str(updates["estado"]))

    # Campos soportados (evita setear atributos inesperados)
    allowed = {
        "proveedor",
        "transportista",
        "patente_camion",
        "nombre_conductor",
        "fecha_hora_cita",
        "observaciones",
        "estado",
    }

    for field, value in updates.items():
        if field not in allowed or value is None:
            continue

        if field in {"proveedor", "transportista", "nombre_conductor", "observaciones"}:
            setattr(cita, field, _norm_text(value))
        elif field == "patente_camion":
            setattr(cita, field, _norm_patente(value))
        elif field == "fecha_hora_cita":
            if not isinstance(value, datetime):
                raise InboundDomainError("fecha_hora_cita debe ser datetime.")
            setattr(cita, field, value)
        # estado ya fue tratado arriba

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

    cita.fecha_hora_llegada_real = fecha_llegada or utcnow()

    # Cambio automático a ARRIBADO si estaba PROGRAMADA o RETRASADO
    if (cita.estado or "").upper() in {ESTADO_PROGRAMADA, ESTADO_RETRASADO}:
        cita.estado = ESTADO_ARRIBADO

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

    recepcion: InboundRecepcion = obtener_recepcion_segura(
        db=db,
        recepcion_id=recepcion_id,
        negocio_id=negocio_id,
    )

    # Si ya está vinculada a otra recepción, bloquear por consistencia
    if cita.recepcion_id and cita.recepcion_id != recepcion.id:
        raise InboundDomainError("Esta cita ya está vinculada a otra recepción.")

    # Si la recepción ya tiene otra cita, bloquear (1:1)
    if getattr(recepcion, "cita_id", None) and recepcion.cita_id != cita.id:
        raise InboundDomainError("La recepción ya tiene una cita asociada.")

    # Relación bidireccional (1:1)
    cita.recepcion_id = recepcion.id
    recepcion.cita_id = cita.id

    db.commit()
    db.refresh(cita)
    return cita
