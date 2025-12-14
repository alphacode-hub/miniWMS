# modules/inbound_orbion/services/services_inbound_fotos.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models.inbound import InboundFoto, InboundLinea, InboundIncidencia
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,
)


# ============================
#   TIME (UTC)
# ============================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================
#   NORMALIZADORES
# ============================

def _clean_str(v: Any) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s or None


def _norm_upper(v: Any) -> str | None:
    s = _clean_str(v)
    return s.upper() if s else None


# ============================
#   POLÍTICA
# ============================

TIPOS_FOTO_VALIDOS: Final[set[str]] = {
    # Ajusta a tus categorías reales
    "EVIDENCIA",
    "DANIO",
    "ETIQUETA",
    "DOCUMENTO",
    "TEMPERATURA",
    "OTRO",
}


@dataclass(frozen=True)
class FotoPolicy:
    require_editable_recepcion: bool = True  # enterprise default


FOTO_POLICY: Final[FotoPolicy] = FotoPolicy()


def _validar_tipo_foto(tipo_norm: str | None) -> str | None:
    if not tipo_norm:
        return None
    if tipo_norm not in TIPOS_FOTO_VALIDOS:
        # No forzamos error si quieres permitir libres. Si prefieres estricto, cambia a raise.
        return tipo_norm
    return tipo_norm


# ============================
#   CRUD
# ============================

def crear_foto_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    ruta_archivo: str,
    tipo: str | None = None,
    descripcion: str | None = None,
    mime_type: str | None = None,
    subido_por_id: int | None = None,
    linea_id: int | None = None,
    incidencia_id: int | None = None,
) -> InboundFoto:
    """
    Crea una foto/evidencia asociada a una recepción, opcionalmente a una línea o incidencia.

    Reglas:
    - Recepción debe pertenecer al negocio.
    - Enterprise default: recepción debe estar editable para adjuntar evidencias.
      (Si quieres permitir fotos incluso con recepción no editable, cambia a obtener_recepcion_segura)
    - Si se entrega linea_id/incidencia_id: deben pertenecer al mismo negocio y a la misma recepción.
    """
    if FOTO_POLICY.require_editable_recepcion:
        recepcion = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    else:
        recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    ruta_norm = _clean_str(ruta_archivo)
    if not ruta_norm:
        raise InboundDomainError("La ruta del archivo de la foto es obligatoria.")

    tipo_norm = _validar_tipo_foto(_norm_upper(tipo))
    descripcion_norm = _clean_str(descripcion)
    mime_type_norm = _clean_str(mime_type)

    foto = InboundFoto(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        ruta_archivo=ruta_norm,
        tipo=tipo_norm,
        descripcion=descripcion_norm,
        mime_type=mime_type_norm,
        subido_por_id=subido_por_id,
    )

    # Campos opcionales (según tu modelo)
    if hasattr(foto, "creado_en"):
        setattr(foto, "creado_en", utcnow())

    # Asociar a línea
    if linea_id is not None:
        if not hasattr(InboundFoto, "linea_id"):
            raise InboundDomainError("Tu modelo InboundFoto no soporta asociación a línea (linea_id).")

        linea = db.get(InboundLinea, int(linea_id))
        if not linea:
            raise InboundDomainError("Línea inbound asociada a la foto no existe.")
        if getattr(linea, "negocio_id", None) != negocio_id:
            raise InboundDomainError("La línea asociada a la foto no pertenece a este negocio.")
        if getattr(linea, "recepcion_id", None) != recepcion.id:
            raise InboundDomainError("La línea asociada a la foto no pertenece a esta recepción.")

        foto.linea_id = linea.id  # type: ignore[attr-defined]

    # Asociar a incidencia
    if incidencia_id is not None:
        if not hasattr(InboundFoto, "incidencia_id"):
            raise InboundDomainError("Tu modelo InboundFoto no soporta asociación a incidencia (incidencia_id).")

        incidencia = db.get(InboundIncidencia, int(incidencia_id))
        if not incidencia:
            raise InboundDomainError("Incidencia inbound asociada a la foto no existe.")
        if getattr(incidencia, "negocio_id", None) != negocio_id:
            raise InboundDomainError("La incidencia asociada a la foto no pertenece a este negocio.")
        if getattr(incidencia, "recepcion_id", None) != recepcion.id:
            raise InboundDomainError("La incidencia asociada a la foto no pertenece a esta recepción.")

        foto.incidencia_id = incidencia.id  # type: ignore[attr-defined]

    db.add(foto)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear la foto (conflicto/duplicado).") from exc

    db.refresh(foto)
    return foto


def eliminar_foto_inbound(
    db: Session,
    negocio_id: int,
    foto_id: int,
) -> None:
    """
    Elimina una foto/evidencia (solo registro en BD).
    Nota: no elimina el archivo físico (eso es responsabilidad de la capa de storage).
    """
    foto = db.get(InboundFoto, foto_id)
    if not foto or foto.negocio_id != negocio_id:
        raise InboundDomainError("Foto inbound no encontrada para este negocio.")

    # Respetar workflow si está ligado a recepción (enterprise)
    if FOTO_POLICY.require_editable_recepcion and getattr(foto, "recepcion_id", None) is not None:
        _ = obtener_recepcion_editable(db, int(foto.recepcion_id), negocio_id)

    db.delete(foto)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise InboundDomainError("No se pudo eliminar la foto.") from exc
