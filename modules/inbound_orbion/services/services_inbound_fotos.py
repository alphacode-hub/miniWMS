from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models import InboundFoto
from core.models.enums import InboundFotoTipo

from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

# =========================================================
# CONFIG STORAGE (enterprise: STORAGE_ROOT)
# =========================================================

_ALLOWED_IMAGE_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB baseline
_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")

# Lectura chunked (evita cargar archivos grandes de golpe)
_READ_CHUNK = 1024 * 1024  # 1MB


def _get_storage_root() -> Path:
    """
    Coherente con Documentos v1: storage local bajo STORAGE_ROOT.
    Si no existe settings.STORAGE_ROOT, usamos <project_root>/storage (fallback seguro).
    """
    project_root = Path(__file__).resolve().parents[3]  # <root>/
    try:
        from core.config import settings  # type: ignore
        sr = getattr(settings, "STORAGE_ROOT", None)
        if sr:
            return Path(sr)
    except Exception:
        pass
    return project_root / "storage"


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

    if fallback_name and "." in fallback_name:
        ext = "." + fallback_name.rsplit(".", 1)[-1].lower()
        if 2 <= len(ext) <= 6:
            return ext
    return ""


def _validate_image_mime(mime: str | None) -> str:
    mt = (mime or "").lower().strip()
    if not mt:
        raise InboundDomainError("No se pudo detectar el tipo de archivo (content_type).")
    if mt not in _ALLOWED_IMAGE_MIME:
        raise InboundDomainError(
            f"Tipo de archivo no permitido: {mt}. Permitidos: JPG/PNG/WEBP."
        )
    return mt


def _storage_paths(
    negocio_id: int,
    recepcion_id: int,
) -> Tuple[Path, str]:
    """
    Devuelve:
      - carpeta absoluta donde escribir
      - storage_rel_dir (relativo a STORAGE_ROOT) para persistir en DB
    """
    storage_root = _get_storage_root()
    rel_dir = Path("inbound") / "fotos" / f"neg_{negocio_id}" / f"rec_{recepcion_id}"
    abs_dir = storage_root / rel_dir
    return abs_dir, rel_dir.as_posix()


async def _read_limited_and_hash(file: UploadFile) -> tuple[bytes, str]:
    """
    Lee el archivo con límite y calcula SHA256.
    Retorna: (raw_bytes, sha256_hex)
    """
    h = hashlib.sha256()
    total = 0
    chunks: list[bytes] = []

    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_SIZE_BYTES:
            raise InboundDomainError(
                f"Archivo demasiado grande. Máximo {_MAX_SIZE_BYTES // (1024 * 1024)}MB."
            )
        h.update(chunk)
        chunks.append(chunk)

    raw = b"".join(chunks)
    return raw, h.hexdigest()


def _try_get_image_dims(raw: bytes, content_type: str) -> tuple[int | None, int | None]:
    """
    Intenta obtener dimensiones (width/height) si Pillow está disponible.
    Si no, retorna (None, None) sin romper baseline.
    """
    try:
        from PIL import Image  # type: ignore
        from io import BytesIO

        with Image.open(BytesIO(raw)) as im:
            w, h = im.size
            # validación mínima
            if w and h and w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        pass
    return None, None


# =========================================================
# LISTAR
# =========================================================

def listar_fotos_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    incluir_inactivas: bool = False,
) -> List[InboundFoto]:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    q = (
        db.query(InboundFoto)
        .filter(InboundFoto.negocio_id == negocio_id)
        .filter(InboundFoto.recepcion_id == recepcion_id)
    )
    if not incluir_inactivas:
        q = q.filter(InboundFoto.activo == 1)

    return q.order_by(InboundFoto.creado_en.desc(), InboundFoto.id.desc()).all()


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
    tipo: InboundFotoTipo = InboundFotoTipo.GENERAL,
    incidencia_id: Optional[int] = None,
    is_principal: bool = False,
    creado_por: Optional[str] = None,
) -> InboundFoto:
    """
    Enterprise v1:
      - valida recepción
      - valida imagen (mime + tamaño)
      - guarda archivo en STORAGE_ROOT/inbound/fotos/neg_x/rec_y/...
      - persiste metadata enterprise en InboundFoto
      - scope principal: recepcion_id (obligatorio)
      - link opcional: incidencia_id
      - soft delete se maneja en eliminar_foto_soft()
    """
    if file is None:
        raise InboundDomainError("Debes seleccionar un archivo.")

    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    content_type = _validate_image_mime(file.content_type)

    raw, sha256_hex = await _read_limited_and_hash(file)

    # dims opcional
    width_px, height_px = _try_get_image_dims(raw, content_type)

    abs_dir, rel_dir = _storage_paths(negocio_id, recepcion_id)
    abs_dir.mkdir(parents=True, exist_ok=True)

    original = _safe_filename(file.filename or "foto")
    ext = _ext_from_mime(content_type, file.filename)

    token = secrets.token_hex(8)
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_{token}_{original}{ext}"

    abs_path = abs_dir / filename
    storage_relpath = f"{rel_dir}/{filename}"

    # escribir binario
    with open(abs_path, "wb") as f:
        f.write(raw)

    foto = InboundFoto(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        incidencia_id=incidencia_id,
        tipo=tipo,
        titulo=(titulo or "").strip() or None,
        nota=(nota or "").strip() or None,
        filename_original=(file.filename or "foto").strip() or "foto",
        storage_relpath=storage_relpath,
        content_type=content_type,
        size_bytes=len(raw),
        sha256=sha256_hex,
        width_px=width_px,
        height_px=height_px,
        version=1,
        is_principal=bool(is_principal),
        creado_por=(creado_por or "").strip() or None,
        creado_en=utcnow(),
        activo=1,
    )

    db.add(foto)
    db.flush()

    return foto


# =========================================================
# RESOLVER PATH (para download/stream en rutas)
# =========================================================

def resolver_foto_storage_path(
    *,
    foto: InboundFoto,
) -> Path:
    """
    Convierte storage_relpath en ruta absoluta del filesystem.
    """
    storage_root = _get_storage_root()
    return storage_root / (foto.storage_relpath or "")


def obtener_foto_segura(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    foto_id: int,
    incluir_inactivas: bool = False,
) -> InboundFoto:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    q = (
        db.query(InboundFoto)
        .filter(InboundFoto.negocio_id == negocio_id)
        .filter(InboundFoto.recepcion_id == recepcion_id)
        .filter(InboundFoto.id == foto_id)
    )
    if not incluir_inactivas:
        q = q.filter(InboundFoto.activo == 1)

    foto = q.first()
    if not foto:
        raise InboundDomainError("Foto no encontrada.")
    return foto


# =========================================================
# ELIMINAR (SOFT DELETE)
# =========================================================

def eliminar_foto_soft(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    foto_id: int,
    eliminado_por: Optional[str] = None,
    motivo_eliminacion: Optional[str] = None,
) -> None:
    """
    Enterprise v1:
      - NO elimina archivo físico (coherente con Documentos v1)
      - marca activo=0 + audit delete
    """
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    foto = obtener_foto_segura(
        db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        foto_id=foto_id,
        incluir_inactivas=True,
    )

    if foto.activo == 0:
        return  # idempotente

    foto.activo = 0
    foto.eliminado_por = (eliminado_por or "").strip() or None
    foto.eliminado_en = utcnow()
    foto.motivo_eliminacion = (motivo_eliminacion or "").strip() or None

    db.add(foto)
    db.flush()
