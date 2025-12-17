# modules/inbound_orbion/services/services_inbound_documentos.py
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import select

from core.models.time import utcnow
from core.models.inbound.documentos import InboundDocumento
from core.models.inbound.recepciones import InboundRecepcion

from .services_inbound_core import InboundDomainError, obtener_recepcion_segura


# =========================================================
# Storage baseline (local disk)
# =========================================================

# Puedes cambiarlo a /data en Railway, o a un volumen.
STORAGE_ROOT = Path(os.getenv("ORBION_STORAGE_DIR", "./storage")).resolve()


def _safe_filename(name: str) -> str:
    """
    Sanitiza nombre para filesystem. Mantiene extensión si existe.
    """
    name = (name or "").strip()
    if not name:
        return "archivo"

    # quita path traversal
    name = name.replace("\\", "/").split("/")[-1]

    # solo allow: letras, números, guion, underscore, punto, espacio (lo normalizamos)
    name = re.sub(r"[^a-zA-Z0-9._\-\s]", "", name).strip()
    name = re.sub(r"\s+", "_", name)
    return name or "archivo"


def _build_path(negocio_id: int, recepcion_id: int, filename: str) -> Path:
    folder = STORAGE_ROOT / "inbound" / f"negocio_{negocio_id}" / f"recepcion_{recepcion_id}"
    folder.mkdir(parents=True, exist_ok=True)

    safe = _safe_filename(filename)
    uniq = uuid.uuid4().hex[:10]
    return folder / f"{uniq}_{safe}"


# =========================================================
# Queries
# =========================================================

def listar_documentos(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
) -> List[InboundDocumento]:
    # valida tenant + existencia recepcion
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    stmt = (
        select(InboundDocumento)
        .where(InboundDocumento.negocio_id == negocio_id)
        .where(InboundDocumento.recepcion_id == recepcion_id)
        .where(InboundDocumento.activo == 1)
        .order_by(InboundDocumento.creado_en.desc())
    )
    return list(db.execute(stmt).scalars().all())


def obtener_documento(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    documento_id: int,
) -> InboundDocumento:
    stmt = (
        select(InboundDocumento)
        .where(InboundDocumento.id == documento_id)
        .where(InboundDocumento.negocio_id == negocio_id)
        .where(InboundDocumento.recepcion_id == recepcion_id)
        .where(InboundDocumento.activo == 1)
    )
    doc = db.execute(stmt).scalar_one_or_none()
    if not doc:
        raise InboundDomainError("Documento no encontrado.")
    return doc


# =========================================================
# Commands
# =========================================================

async def crear_documento(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    *,
    tipo: str,
    descripcion: Optional[str],
    file: UploadFile,
    creado_por: Optional[str],
) -> InboundDocumento:
    if not tipo or not tipo.strip():
        raise InboundDomainError("Debes seleccionar un tipo de documento.")

    # valida recepcion (tenant)
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    if file is None or not file.filename:
        raise InboundDomainError("Debes adjuntar un archivo.")

    dest = _build_path(negocio_id, recepcion_id, file.filename)

    # Guardar archivo en disco (stream)
    try:
        contents = await file.read()
        if not contents:
            raise InboundDomainError("El archivo está vacío.")
        dest.write_bytes(contents)
    except InboundDomainError:
        raise
    except Exception:
        raise InboundDomainError("No fue posible guardar el archivo. Revisa permisos/storage.")

    doc = InboundDocumento(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        tipo=tipo.strip(),
        nombre=_safe_filename(file.filename),
        mime_type=(file.content_type or None),
        uri=str(dest),
        descripcion=(descripcion.strip() if isinstance(descripcion, str) and descripcion.strip() else None),
        creado_por=(creado_por.strip() if isinstance(creado_por, str) and creado_por.strip() else None),
        creado_en=utcnow(),
        activo=1,
    )
    db.add(doc)
    db.flush()
    return doc


def eliminar_documento(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    documento_id: int,
) -> InboundDocumento:
    doc = obtener_documento(db, negocio_id=negocio_id, recepcion_id=recepcion_id, documento_id=documento_id)

    # Soft delete (baseline)
    doc.activo = 0
    db.flush()
    return doc
