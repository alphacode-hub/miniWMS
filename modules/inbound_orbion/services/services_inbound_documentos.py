# modules/inbound_orbion/services/services_inbound_documentos.py
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Optional, List, Tuple

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models.enums import InboundDocumentoEstado, InboundDocumentoTipo
from core.models.inbound.documentos import InboundDocumento

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

    # allow: letras, números, guion, underscore, punto, espacio (lo normalizamos)
    name = re.sub(r"[^a-zA-Z0-9._\-\s]", "", name).strip()
    name = re.sub(r"\s+", "_", name)
    return name or "archivo"


def _build_path(negocio_id: int, recepcion_id: int, filename: str) -> Path:
    folder = STORAGE_ROOT / "inbound" / f"negocio_{negocio_id}" / f"recepcion_{recepcion_id}"
    folder.mkdir(parents=True, exist_ok=True)

    safe = _safe_filename(filename)
    uniq = uuid.uuid4().hex[:10]
    return folder / f"{uniq}_{safe}"


def _rel_uri(path: Path) -> str:
    """
    Guarda el uri de forma portable (relativo a STORAGE_ROOT).
    Evita rutas absolutas en DB (mejor para despliegues).
    """
    try:
        return str(path.relative_to(STORAGE_ROOT)).replace("\\", "/")
    except Exception:
        # fallback: si por alguna razón no es relativo
        return str(path).replace("\\", "/")


def _parse_tipo(tipo: str) -> InboundDocumentoTipo:
    """
    Acepta inputs de UI tipo:
    - "GUIA", "guia"
    - "FACTURA", "factura"
    - "BL", "bl"
    - "CERTIFICADO", "certificado"
    - "OTRO", "otro"
    """
    if not tipo or not tipo.strip():
        raise InboundDomainError("Debes seleccionar un tipo de documento.")

    t = tipo.strip().upper()

    # normalizaciones comunes
    if t in ("GUIA", "GUIA_DESPACHO", "GD"):
        return InboundDocumentoTipo.GUIA
    if t in ("FACTURA", "FAC"):
        return InboundDocumentoTipo.FACTURA
    if t in ("BL", "B/L"):
        return InboundDocumentoTipo.BL
    if t in ("CERTIFICADO", "CERT", "CERTIFICADOS"):
        return InboundDocumentoTipo.CERTIFICADO
    if t in ("OTRO", "OTROS"):
        return InboundDocumentoTipo.OTRO

    raise InboundDomainError("Tipo de documento inválido.")


async def _save_upload_streaming(file: UploadFile, dest: Path) -> Tuple[int, str]:
    """
    Guarda un UploadFile a disco en streaming, calculando sha256 y size_bytes.

    Retorna:
      (size_bytes, sha256_hex)
    """
    hasher = hashlib.sha256()
    size = 0

    try:
        # Asegura puntero al inicio si se reutiliza (por seguridad)
        try:
            await file.seek(0)
        except Exception:
            pass

        with dest.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)
                size += len(chunk)

        if size <= 0:
            # limpia archivo vacío si se creó
            try:
                dest.unlink(missing_ok=True)  # py3.8+? (si falla, ignoramos)
            except Exception:
                try:
                    if dest.exists():
                        dest.unlink()
                except Exception:
                    pass
            raise InboundDomainError("El archivo está vacío.")

        return size, hasher.hexdigest()

    except InboundDomainError:
        raise
    except Exception:
        # si falló escritura, intentamos limpiar
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise InboundDomainError("No fue posible guardar el archivo. Revisa permisos/storage.")


def _assert_recepcion_editable(recepcion) -> None:
    """
    Baseline guard: no permitir cambios si la recepción está cerrada o cancelada.
    No importa el tipo exacto del Enum: comparamos por .value cuando exista.
    """
    estado = None
    try:
        estado = recepcion.estado.value if recepcion and recepcion.estado else None
    except Exception:
        estado = getattr(recepcion, "estado", None)

    if estado in ("CERRADO", "CANCELADO"):
        raise InboundDomainError("La recepción está cerrada/cancelada. No se pueden modificar documentos.")


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
        .where(InboundDocumento.is_deleted.is_(False))
        .order_by(InboundDocumento.created_at.desc())
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
        .where(InboundDocumento.is_deleted.is_(False))
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
    linea_id: Optional[int] = None,
    pallet_id: Optional[int] = None,
) -> InboundDocumento:
    # valida recepcion (tenant) + editable
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
    _assert_recepcion_editable(recepcion)

    if file is None or not file.filename:
        raise InboundDomainError("Debes adjuntar un archivo.")

    tipo_enum = _parse_tipo(tipo)

    filename_safe = _safe_filename(file.filename)
    dest = _build_path(negocio_id, recepcion_id, filename_safe)

    size_bytes, sha256_hex = await _save_upload_streaming(file, dest)

    # ===========================
    # Versionado simple por grupo
    # ===========================
    # Regla baseline:
    # - Si ya existe un documento VIGENTE "actual" con mismo tipo + nombre (en la recepción),
    #   entonces: reutilizamos doc_group_id, incrementamos version, marcamos anterior como REEMPLAZADO
    # - Si no existe, creamos grupo nuevo.
    stmt_current = (
        select(InboundDocumento)
        .where(InboundDocumento.negocio_id == negocio_id)
        .where(InboundDocumento.recepcion_id == recepcion_id)
        .where(InboundDocumento.tipo == tipo_enum)
        .where(InboundDocumento.nombre == filename_safe)
        .where(InboundDocumento.is_current.is_(True))
        .where(InboundDocumento.activo == 1)
        .where(InboundDocumento.is_deleted.is_(False))
        .order_by(InboundDocumento.created_at.desc())
    )
    current = db.execute(stmt_current).scalar_one_or_none()

    if current:
        group_id = current.doc_group_id or uuid.uuid4().hex
        next_version = int(current.version or 1) + 1

        current.is_current = False
        current.estado = InboundDocumentoEstado.REEMPLAZADO
        current.updated_at = utcnow()
    else:
        group_id = uuid.uuid4().hex
        next_version = 1

    doc = InboundDocumento(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        linea_id=linea_id,
        pallet_id=pallet_id,
        doc_group_id=group_id,
        version=next_version,
        is_current=True,
        tipo=tipo_enum,
        estado=InboundDocumentoEstado.VIGENTE,
        nombre=filename_safe,
        mime_type=(file.content_type or None),
        uri=_rel_uri(dest),
        descripcion=(descripcion.strip() if isinstance(descripcion, str) and descripcion.strip() else None),
        creado_por=(creado_por.strip() if isinstance(creado_por, str) and creado_por.strip() else None),
        created_at=utcnow(),
        size_bytes=size_bytes,
        sha256=sha256_hex,
        activo=1,
        is_deleted=False,
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
    # valida recepcion + editable
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
    _assert_recepcion_editable(recepcion)

    doc = obtener_documento(db, negocio_id=negocio_id, recepcion_id=recepcion_id, documento_id=documento_id)

    # Soft delete (baseline + trazabilidad)
    doc.activo = 0
    doc.is_deleted = True
    doc.deleted_at = utcnow()
    doc.updated_at = utcnow()

    # Si era la versión vigente del grupo, promovemos la última versión anterior activa/no borrada
    if doc.is_current and doc.doc_group_id:
        stmt_prev = (
            select(InboundDocumento)
            .where(InboundDocumento.negocio_id == negocio_id)
            .where(InboundDocumento.recepcion_id == recepcion_id)
            .where(InboundDocumento.doc_group_id == doc.doc_group_id)
            .where(InboundDocumento.id != doc.id)
            .where(InboundDocumento.activo == 1)
            .where(InboundDocumento.is_deleted.is_(False))
            .order_by(InboundDocumento.version.desc(), InboundDocumento.created_at.desc())
        )
        prev = db.execute(stmt_prev).scalar_one_or_none()
        if prev:
            prev.is_current = True
            # si estaba reemplazado, lo devolvemos a vigente
            prev.estado = InboundDocumentoEstado.VIGENTE
            prev.updated_at = utcnow()

    doc.is_current = False
    db.flush()
    return doc
