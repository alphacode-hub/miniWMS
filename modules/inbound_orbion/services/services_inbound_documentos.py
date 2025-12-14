# modules/inbound_orbion/services/services_inbound_documentos.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models.inbound import InboundDocumento
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
#   TIPOS / NORMALIZACIÓN
# ============================

TIPOS_DOCUMENTO_VALIDOS: Final[set[str]] = {
    "GUIA",
    "FACTURA",
    "PACKING_LIST",
    "CERTIFICADO",
    "OTRO",
}


def _normalizar_tipo_documento(tipo: str | None) -> str:
    tipo_norm = (tipo or "").strip().upper()
    if tipo_norm not in TIPOS_DOCUMENTO_VALIDOS:
        raise InboundDomainError(
            f"Tipo de documento inválido: {tipo}. "
            f"Debe ser uno de {', '.join(sorted(TIPOS_DOCUMENTO_VALIDOS))}."
        )
    return tipo_norm


def _clean_str(v: Any) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s or None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"true", "on", "1", "si", "sí", "yes", "y"}


# ============================
#   POLÍTICA DE UPDATE
# ============================

@dataclass(frozen=True)
class DocumentoUpdatePolicy:
    """
    Campos permitidos a modificar en un documento.
    Nota: negocio_id / recepcion_id / id no se tocan.
    """
    allowed_fields: frozenset[str] = frozenset(
        {
            "tipo",
            "nombre_archivo",
            "ruta_archivo",
            "mime_type",
            "es_obligatorio",
            "es_validado",
            "observaciones",
        }
    )


UPDATE_POLICY: Final[DocumentoUpdatePolicy] = DocumentoUpdatePolicy()


# ============================
#   CRUD DOCUMENTOS INBOUND
# ============================

def crear_documento_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    tipo: str,
    nombre_archivo: str,
    ruta_archivo: str,
    mime_type: str | None = None,
    es_obligatorio: bool = False,
    observaciones: str | None = None,
    subido_por_id: int | None = None,
) -> InboundDocumento:
    """
    Crea un documento asociado a una recepción inbound del negocio.

    Reglas:
    - Recepción debe pertenecer al negocio.
    - (Enterprise) Para mutaciones, la recepción debe estar editable según workflow.
      Si quieres permitir subir docs incluso en control de calidad/cerrado, cambia a obtener_recepcion_segura().
    """
    # Si prefieres permitir adjuntar documentos aunque la recepción esté no editable, usa obtener_recepcion_segura.
    recepcion = obtener_recepcion_editable(db, recepcion_id, negocio_id)

    nombre_archivo_norm = _clean_str(nombre_archivo)
    ruta_archivo_norm = _clean_str(ruta_archivo)

    if not nombre_archivo_norm or not ruta_archivo_norm:
        raise InboundDomainError("Nombre de archivo y ruta de archivo son obligatorios.")

    doc = InboundDocumento(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        tipo=_normalizar_tipo_documento(tipo),
        nombre_archivo=nombre_archivo_norm,
        ruta_archivo=ruta_archivo_norm,
        mime_type=_clean_str(mime_type),
        es_obligatorio=_to_bool(es_obligatorio),
        es_validado=False,
        subido_por_id=subido_por_id,
        observaciones=_clean_str(observaciones),
        creado_en=utcnow() if hasattr(InboundDocumento, "creado_en") else None,  # compat
    )

    # Si el modelo no tiene creado_en, el None será ignorado por SQLAlchemy si no existe el atributo.
    if not hasattr(doc, "creado_en"):
        # Quita el atributo dinámico para evitar warnings si tu modelo no lo define.
        try:
            delattr(doc, "creado_en")  # type: ignore[attr-defined]
        except Exception:
            pass

    db.add(doc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear el documento (conflicto/duplicado).") from exc

    db.refresh(doc)
    return doc


def actualizar_documento_inbound(
    db: Session,
    negocio_id: int,
    documento_id: int,
    **updates: Any,
) -> InboundDocumento:
    """
    Actualiza campos permitidos de un documento inbound existente.
    """
    doc = db.get(InboundDocumento, documento_id)
    if not doc or doc.negocio_id != negocio_id:
        raise InboundDomainError("Documento inbound no encontrado para este negocio.")

    # Regla enterprise: si el doc cuelga de una recepción, validamos pertenencia + editabilidad.
    # (Si tu operación de update debe permitirse siempre, cambia a obtener_recepcion_segura)
    if getattr(doc, "recepcion_id", None) is not None:
        _ = obtener_recepcion_editable(db, int(doc.recepcion_id), negocio_id)

    safe_updates: dict[str, Any] = {}
    for field, value in updates.items():
        if field not in UPDATE_POLICY.allowed_fields:
            continue
        safe_updates[field] = value

    if "tipo" in safe_updates and safe_updates["tipo"] is not None:
        safe_updates["tipo"] = _normalizar_tipo_documento(safe_updates["tipo"])

    for field, value in safe_updates.items():
        if field in {"nombre_archivo", "ruta_archivo", "mime_type", "observaciones"}:
            value = _clean_str(value)

        if field in {"es_obligatorio", "es_validado"}:
            value = _to_bool(value)

        setattr(doc, field, value)

    # (Opcional) audit metadata
    if hasattr(doc, "actualizado_en"):
        setattr(doc, "actualizado_en", utcnow())

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar el documento (conflicto/duplicado).") from exc

    db.refresh(doc)
    return doc


def marcar_documento_validado(
    db: Session,
    negocio_id: int,
    documento_id: int,
    es_validado: bool = True,
) -> InboundDocumento:
    """
    Marca (o desmarca) un documento como validado.
    """
    return actualizar_documento_inbound(
        db=db,
        negocio_id=negocio_id,
        documento_id=documento_id,
        es_validado=es_validado,
    )


def eliminar_documento_inbound(
    db: Session,
    negocio_id: int,
    documento_id: int,
) -> None:
    """
    Elimina un documento inbound (solo registro en BD).
    """
    doc = db.get(InboundDocumento, documento_id)
    if not doc or doc.negocio_id != negocio_id:
        raise InboundDomainError("Documento inbound no encontrado para este negocio.")

    # Regla enterprise: mutación ligada a recepción -> respetar workflow
    if getattr(doc, "recepcion_id", None) is not None:
        _ = obtener_recepcion_editable(db, int(doc.recepcion_id), negocio_id)

    db.delete(doc)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise InboundDomainError("No se pudo eliminar el documento.") from exc
