# modules/inbound_orbion/services/services_inbound_documentos.py

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.models import InboundDocumento
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

TIPOS_DOCUMENTO_VALIDOS = {
    "GUIA",
    "FACTURA",
    "PACKING_LIST",
    "CERTIFICADO",
    "OTRO",
}


def _normalizar_tipo_documento(tipo: str) -> str:
    tipo_norm = (tipo or "").strip().upper()
    if tipo_norm not in TIPOS_DOCUMENTO_VALIDOS:
        raise InboundDomainError(
            f"Tipo de documento inválido: {tipo}. "
            f"Debe ser uno de {', '.join(sorted(TIPOS_DOCUMENTO_VALIDOS))}."
        )
    return tipo_norm


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
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    if not nombre_archivo or not ruta_archivo:
        raise InboundDomainError("Nombre de archivo y ruta de archivo son obligatorios.")

    doc = InboundDocumento(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        tipo=_normalizar_tipo_documento(tipo),
        nombre_archivo=nombre_archivo,
        ruta_archivo=ruta_archivo,
        mime_type=mime_type,
        es_obligatorio=bool(es_obligatorio),
        es_validado=False,
        subido_por_id=subido_por_id,
        observaciones=(observaciones or "").strip() or None,
    )

    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def actualizar_documento_inbound(
    db: Session,
    negocio_id: int,
    documento_id: int,
    **updates: Any,
) -> InboundDocumento:
    doc = db.get(InboundDocumento, documento_id)
    if not doc or doc.negocio_id != negocio_id:
        raise InboundDomainError("Documento inbound no encontrado para este negocio.")

    if "tipo" in updates and updates["tipo"] is not None:
        updates["tipo"] = _normalizar_tipo_documento(updates["tipo"])

    for field, value in updates.items():
        if not hasattr(doc, field):
            continue
        setattr(doc, field, value)

    db.commit()
    db.refresh(doc)
    return doc


def marcar_documento_validado(
    db: Session,
    negocio_id: int,
    documento_id: int,
    es_validado: bool = True,
) -> InboundDocumento:
    doc = db.get(InboundDocumento, documento_id)
    if not doc or doc.negocio_id != negocio_id:
        raise InboundDomainError("Documento inbound no encontrado para este negocio.")

    doc.es_validado = bool(es_validado)
    db.commit()
    db.refresh(doc)
    return doc


def eliminar_documento_inbound(
    db: Session,
    negocio_id: int,
    documento_id: int,
) -> None:
    doc = db.get(InboundDocumento, documento_id)
    if not doc or doc.negocio_id != negocio_id:
        raise InboundDomainError("Documento inbound no encontrado para este negocio.")

    db.delete(doc)
    db.commit()
