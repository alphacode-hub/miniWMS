# modules/inbound_orbion/services/services_inbound_fotos.py
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models import InboundFoto

from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

# =========================================================
# CONFIG STORAGE (baseline v1: filesystem bajo /static)
# =========================================================

_ALLOWED_IMAGE_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

_ALLOWED_OTHER_MIME = {
    "application/pdf",
}

_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB baseline
_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "archivo"
    name = name.replace(" ", "_")
    name = _FILENAME_SAFE_RE.sub("", name)
    return name[:160] or "archivo"


def _ext_from_mime(mime: str | None, fallback_name: str | None) -> str:
    mime = (mime or "").lower().strip()
    if mime == "image/jpeg":
        return ".jpg"
    if mime == "image/png":
        return ".png"
    if mime == "image/webp":
        return ".webp"
    if mime == "application/pdf":
        return ".pdf"

    # fallback por filename
    if fallback_name and "." in fallback_name:
        ext = "." + fallback_name.rsplit(".", 1)[-1].lower()
        if len(ext) <= 6:
            return ext
    return ""


def _storage_paths(
    negocio_id: int,
    recepcion_id: int,
) -> Tuple[Path, str]:
    """
    Devuelve:
      - carpeta absoluta de escritura (dentro de /static/uploads/...)
      - base_url pública para servir
    """
    project_root = Path(__file__).resolve().parents[3]  # <root>/
    static_dir = project_root / "static"
    uploads_dir = static_dir / "uploads" / "inbound" / "fotos" / f"neg_{negocio_id}" / f"rec_{recepcion_id}"
    base_url = f"/static/uploads/inbound/fotos/neg_{negocio_id}/rec_{recepcion_id}"
    return uploads_dir, base_url


async def _read_limited(file: UploadFile) -> bytes:
    data = await file.read()
    if data is None:
        data = b""
    if len(data) > _MAX_SIZE_BYTES:
        raise InboundDomainError(f"Archivo demasiado grande. Máximo {_MAX_SIZE_BYTES // (1024*1024)}MB.")
    return data


def _validate_mime(mime: str | None) -> None:
    mt = (mime or "").lower().strip()
    if not mt:
        raise InboundDomainError("No se pudo detectar el tipo de archivo (mime_type).")
    if mt not in _ALLOWED_IMAGE_MIME and mt not in _ALLOWED_OTHER_MIME:
        raise InboundDomainError(
            f"Tipo de archivo no permitido: {mt}. "
            f"Permitidos: JPG/PNG/WEBP y PDF."
        )


# =========================================================
# LISTAR
# =========================================================

def listar_fotos_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> List[InboundFoto]:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    return (
        db.query(InboundFoto)
        .filter(InboundFoto.negocio_id == negocio_id)
        .filter(InboundFoto.recepcion_id == recepcion_id)
        .order_by(InboundFoto.creado_en.desc(), InboundFoto.id.desc())
        .all()
    )


# =========================================================
# CREAR (UPLOAD)
# =========================================================

async def crear_foto_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    file: UploadFile,
    titulo: Optional[str] = None,
    nota: Optional[str] = None,
) -> InboundFoto:
    """
    Baseline v1:
      - guarda archivo en static/uploads/...
      - persiste metadata en InboundFoto
      - scope = recepcion_id
    """
    if file is None:
        raise InboundDomainError("Debes seleccionar un archivo.")

    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    mime = (file.content_type or "").strip().lower()
    _validate_mime(mime)

    raw = await _read_limited(file)

    uploads_dir, base_url = _storage_paths(negocio_id, recepcion_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    original = _safe_filename(file.filename or "foto")
    ext = _ext_from_mime(mime, file.filename)

    token = secrets.token_hex(8)
    filename = f"{utcnow().strftime('%Y%m%d_%H%M%S')}_{token}_{original}{ext}"
    abs_path = uploads_dir / filename
    rel_url = f"{base_url}/{filename}"

    # escribir binario
    with open(abs_path, "wb") as f:
        f.write(raw)

    foto = InboundFoto(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        titulo=(titulo or "").strip() or None,
        nota=(nota or "").strip() or None,
        archivo_url=rel_url,
        archivo_path=str(abs_path.relative_to(Path(__file__).resolve().parents[3])),  # path relativo al root
        mime_type=mime,
        size_bytes=len(raw),
        creado_en=utcnow(),
    )
    db.add(foto)
    db.flush()
    return foto


# =========================================================
# ELIMINAR
# =========================================================

def eliminar_foto(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    foto_id: int,
) -> None:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    foto = (
        db.query(InboundFoto)
        .filter(InboundFoto.negocio_id == negocio_id)
        .filter(InboundFoto.recepcion_id == recepcion_id)
        .filter(InboundFoto.id == foto_id)
        .first()
    )
    if not foto:
        raise InboundDomainError("Foto no encontrada.")

    # intenta borrar archivo (best effort)
    try:
        if foto.archivo_path:
            project_root = Path(__file__).resolve().parents[3]
            abs_path = project_root / foto.archivo_path
            if abs_path.exists() and abs_path.is_file():
                abs_path.unlink()
    except Exception:
        # no bloqueamos la UI por falla de filesystem
        pass

    db.delete(foto)
    db.flush()
