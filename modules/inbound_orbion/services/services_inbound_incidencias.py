# modules/inbound_orbion/services/services_inbound_incidencias.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models.inbound import InboundIncidencia
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
)


# ============================
#   TIME (UTC)
# ============================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================
#   NORMALIZADORES / VALIDACIÓN
# ============================

CRITICIDADES_VALIDAS: Final[set[str]] = {"baja", "media", "alta"}


def _clean_str(v: Any) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s or None


def _norm_criticidad(v: Any) -> str:
    s = _clean_str(v)
    s = (s or "").lower()
    if s not in CRITICIDADES_VALIDAS:
        raise InboundDomainError("La criticidad debe ser baja, media o alta.")
    return s


def _norm_tipo(v: Any) -> str:
    s = _clean_str(v)
    if not s:
        raise InboundDomainError("El tipo de incidencia es obligatorio.")
    return s


def _norm_descripcion(v: Any) -> str:
    s = _clean_str(v)
    if not s:
        raise InboundDomainError("La descripción de la incidencia es obligatoria.")
    return s


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
    *,
    creado_por_id: int | None = None,
    origen: str | None = "inbound",
    cerrado: bool | None = None,
) -> InboundIncidencia:
    """
    Crea una incidencia asociada a una recepción inbound del negocio.

    Enterprise rules:
    - Recepción debe pertenecer al negocio.
    - Default: recepción debe estar editable para agregar incidencias.
      (si quieres permitir incidencias incluso post-cierre, cambia a obtener_recepcion_segura)
    - criticidad ∈ {baja, media, alta}
    """
    recepcion = obtener_recepcion_editable(db, recepcion_id, negocio_id)

    incidencia_kwargs: dict[str, Any] = {
        "recepcion_id": recepcion.id,
        "tipo": _norm_tipo(tipo),
        "criticidad": _norm_criticidad(criticidad),
        "descripcion": _norm_descripcion(descripcion),
    }

    # Multi-tenant estricto si el modelo lo soporta
    if hasattr(InboundIncidencia, "negocio_id"):
        incidencia_kwargs["negocio_id"] = negocio_id

    # Campos opcionales si existen en el modelo
    if creado_por_id is not None and hasattr(InboundIncidencia, "creado_por_id"):
        incidencia_kwargs["creado_por_id"] = creado_por_id
    if origen is not None and hasattr(InboundIncidencia, "origen"):
        incidencia_kwargs["origen"] = (origen or "").strip() or None
    if cerrado is not None and hasattr(InboundIncidencia, "cerrado"):
        incidencia_kwargs["cerrado"] = bool(cerrado)

    if hasattr(InboundIncidencia, "creado_en"):
        incidencia_kwargs["creado_en"] = utcnow()

    incidencia = InboundIncidencia(**incidencia_kwargs)

    db.add(incidencia)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear la incidencia (conflicto/duplicado).") from exc

    db.refresh(incidencia)
    return incidencia


def eliminar_incidencia_inbound(
    db: Session,
    negocio_id: int,
    incidencia_id: int,
) -> None:
    """
    Elimina una incidencia inbound, validando:
    - Que exista.
    - Multi-tenant (si el modelo tiene negocio_id).
    - Que la recepción sea editable (enterprise default).
    """
    incidencia = db.get(InboundIncidencia, incidencia_id)
    if not incidencia:
        raise InboundDomainError("Incidencia inbound no encontrada.")

    if hasattr(InboundIncidencia, "negocio_id") and getattr(incidencia, "negocio_id", None) != negocio_id:
        raise InboundDomainError("Incidencia inbound no pertenece a este negocio.")

    recepcion_id = getattr(incidencia, "recepcion_id", None)
    if recepcion_id is None:
        raise InboundDomainError("Incidencia inválida: falta recepcion_id.")

    # Workflow: por defecto, no permitimos borrar si la recepción no es editable
    _ = obtener_recepcion_editable(db, int(recepcion_id), negocio_id)

    try:
        db.delete(incidencia)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise InboundDomainError("No se pudo eliminar la incidencia.") from exc


def obtener_incidencia_segura(
    db: Session,
    negocio_id: int,
    incidencia_id: int,
) -> InboundIncidencia:
    """
    Helper: obtiene incidencia y valida pertenencia al negocio (si aplica).
    Útil para rutas de edición/adjuntos/fotos.
    """
    incidencia = db.get(InboundIncidencia, incidencia_id)
    if not incidencia:
        raise InboundDomainError("Incidencia inbound no encontrada.")

    if hasattr(InboundIncidencia, "negocio_id") and getattr(incidencia, "negocio_id", None) != negocio_id:
        raise InboundDomainError("Incidencia inbound no pertenece a este negocio.")

    # Verificamos que la recepción exista y sea del negocio (consistencia)
    _ = obtener_recepcion_segura(db, int(getattr(incidencia, "recepcion_id")), negocio_id)
    return incidencia
