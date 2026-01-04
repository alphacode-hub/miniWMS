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

from core.logging_config import logger
from core.models import Negocio
from core.models.time import utcnow
from core.models.enums import InboundDocumentoEstado, InboundDocumentoTipo, ModuleKey, UsageCounterType
from core.models.inbound.documentos import InboundDocumento
from core.services.services_entitlements import resolve_entitlements
from core.services.services_usage import get_usage_value, increment_usage_dual

from .services_inbound_core import InboundDomainError, obtener_recepcion_segura


STORAGE_ROOT = Path(os.getenv("ORBION_STORAGE_DIR", "./storage")).resolve()

_METRIC_EVIDENCIAS_MB = "evidencias_mb"
_READ_CHUNK = 1024 * 1024  # 1MB
_MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25MB por documento (ajustable)


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "archivo"
    name = name.replace("\\", "/").split("/")[-1]
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
    try:
        return str(path.relative_to(STORAGE_ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _abs_from_uri(uri: str) -> Path:
    u = (uri or "").strip().replace("\\", "/")
    if not u:
        raise InboundDomainError("Documento inválido: uri vacía.")
    u = u.lstrip("/")
    abs_path = (STORAGE_ROOT / u).resolve()
    try:
        abs_path.relative_to(STORAGE_ROOT.resolve())
    except Exception:
        raise InboundDomainError("Documento inválido: ruta no permitida.")
    return abs_path


def _bytes_to_mb(size_bytes: int) -> float:
    try:
        b = float(size_bytes or 0)
    except Exception:
        b = 0.0
    if b <= 0:
        return 0.0
    return b / (1024.0 * 1024.0)


def _get_inbound_limits(ent: dict) -> dict:
    limits_all = ent.get("limits")
    if not isinstance(limits_all, dict):
        return {}
    inbound = limits_all.get("inbound")
    return inbound if isinstance(inbound, dict) else {}


def _coerce_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip().replace(",", "."))
    except Exception:
        return None


def _parse_tipo(tipo: str) -> InboundDocumentoTipo:
    if not tipo or not tipo.strip():
        raise InboundDomainError("Debes seleccionar un tipo de documento.")

    t = tipo.strip().upper()

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
    hasher = hashlib.sha256()
    size = 0

    try:
        try:
            await file.seek(0)
        except Exception:
            pass

        with dest.open("wb") as f:
            while True:
                chunk = await file.read(_READ_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_SIZE_BYTES:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise InboundDomainError(f"Archivo demasiado grande. Máximo {_MAX_SIZE_BYTES // (1024*1024)}MB.")
                f.write(chunk)
                hasher.update(chunk)

        if size <= 0:
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
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise InboundDomainError("No fue posible guardar el archivo. Revisa permisos/storage.")


def _assert_recepcion_editable(recepcion) -> None:
    try:
        estado = recepcion.estado.value if recepcion and recepcion.estado else None
    except Exception:
        estado = getattr(recepcion, "estado", None)

    if estado in ("CERRADO", "CANCELADO"):
        raise InboundDomainError("La recepción está cerrada/cancelada. No se pueden modificar documentos.")


def _enforce_evidencias_mb(
    db: Session,
    *,
    negocio_id: int,
    size_bytes: int,
    negocio: Negocio | None = None,
) -> None:
    try:
        n = negocio or db.query(Negocio).filter(Negocio.id == int(negocio_id)).first()
    except Exception:
        n = None

    if not n:
        return

    ent = resolve_entitlements(n)
    inbound_limits = _get_inbound_limits(ent)
    max_mb = _coerce_float(inbound_limits.get("evidencias_mb"))

    if max_mb is None:
        return

    mb = _bytes_to_mb(size_bytes)
    if mb <= 0:
        return

    usado = get_usage_value(
        db,
        negocio_id=int(negocio_id),
        module_key=ModuleKey.INBOUND,
        metric_key=_METRIC_EVIDENCIAS_MB,
        counter_type=UsageCounterType.BILLABLE,
    )

    if float(usado or 0.0) + float(mb) > float(max_mb) + 1e-9:
        remaining = max(0.0, float(max_mb) - float(usado or 0.0))
        raise InboundDomainError(
            f"Límite de evidencias excedido. Disponible: {remaining:.2f} MB (de {float(max_mb):.0f} MB)."
        )


# =========================================================
# Queries
# =========================================================

def listar_documentos(db: Session, negocio_id: int, recepcion_id: int) -> List[InboundDocumento]:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    stmt = (
        select(InboundDocumento)
        .where(InboundDocumento.negocio_id == negocio_id)
        .where(InboundDocumento.recepcion_id == recepcion_id)
        .where(InboundDocumento.activo == 1)
        .where(InboundDocumento.is_deleted.is_(False))
        .order_by(InboundDocumento.created_at.desc(), InboundDocumento.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def obtener_documento(db: Session, negocio_id: int, recepcion_id: int, documento_id: int) -> InboundDocumento:
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
    negocio: Negocio | None = None,
) -> InboundDocumento:
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
    _assert_recepcion_editable(recepcion)

    if file is None or not file.filename:
        raise InboundDomainError("Debes adjuntar un archivo.")

    tipo_enum = _parse_tipo(tipo)

    # ✅ pre-enforce conservador (evita escribir si ya estás pasado)
    if negocio is not None:
        _enforce_evidencias_mb(db, negocio_id=negocio_id, size_bytes=_MAX_SIZE_BYTES, negocio=negocio)

    filename_safe = _safe_filename(file.filename)
    dest = _build_path(negocio_id, recepcion_id, filename_safe)

    size_bytes, sha256_hex = await _save_upload_streaming(file, dest)

    # ✅ enforce real con size final
    try:
        _enforce_evidencias_mb(db, negocio_id=negocio_id, size_bytes=size_bytes, negocio=negocio)
    except Exception:
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise

    # buscar current del mismo tipo+nombre (baseline simple)
    stmt_current = (
        select(InboundDocumento)
        .where(InboundDocumento.negocio_id == negocio_id)
        .where(InboundDocumento.recepcion_id == recepcion_id)
        .where(InboundDocumento.tipo == tipo_enum)
        .where(InboundDocumento.nombre == filename_safe)
        .where(InboundDocumento.is_current.is_(True))
        .where(InboundDocumento.activo == 1)
        .where(InboundDocumento.is_deleted.is_(False))
        .order_by(InboundDocumento.created_at.desc(), InboundDocumento.id.desc())
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

    try:
        db.add(doc)
        db.flush()

        mb = _bytes_to_mb(size_bytes)
        if mb > 0:
            try:
                increment_usage_dual(
                    db,
                    negocio_id=int(negocio_id),
                    module_key=ModuleKey.INBOUND,
                    metric_key=_METRIC_EVIDENCIAS_MB,
                    delta=float(mb),
                )
            except Exception:
                # resiliente
                pass

        logger.info(
            "[INBOUND][DOC] creado negocio_id=%s recepcion_id=%s doc_id=%s bytes=%s mb=%.3f tipo=%s",
            negocio_id,
            recepcion_id,
            getattr(doc, "id", None),
            size_bytes,
            mb,
            str(tipo_enum),
        )

        return doc

    except Exception as exc:
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise InboundDomainError("No se pudo registrar el documento en la base de datos.") from exc


def eliminar_documento(db: Session, negocio_id: int, recepcion_id: int, documento_id: int) -> InboundDocumento:
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
    _assert_recepcion_editable(recepcion)

    doc = obtener_documento(db, negocio_id=negocio_id, recepcion_id=recepcion_id, documento_id=documento_id)

    doc.activo = 0
    doc.is_deleted = True
    doc.deleted_at = utcnow()
    doc.updated_at = utcnow()

    # borrar físico (opcional, resiliente)
    try:
        abs_path = _abs_from_uri(getattr(doc, "uri", "") or "")
        if abs_path.exists() and abs_path.is_file():
            abs_path.unlink()
    except Exception:
        pass

    # promover versión anterior si corresponde
    if doc.is_current and doc.doc_group_id:
        stmt_prev = (
            select(InboundDocumento)
            .where(InboundDocumento.negocio_id == negocio_id)
            .where(InboundDocumento.recepcion_id == recepcion_id)
            .where(InboundDocumento.doc_group_id == doc.doc_group_id)
            .where(InboundDocumento.id != doc.id)
            .where(InboundDocumento.activo == 1)
            .where(InboundDocumento.is_deleted.is_(False))
            .order_by(InboundDocumento.version.desc(), InboundDocumento.created_at.desc(), InboundDocumento.id.desc())
        )
        prev = db.execute(stmt_prev).scalar_one_or_none()
        if prev:
            prev.is_current = True
            prev.estado = InboundDocumentoEstado.VIGENTE
            prev.updated_at = utcnow()

    doc.is_current = False
    db.flush()
    return doc
