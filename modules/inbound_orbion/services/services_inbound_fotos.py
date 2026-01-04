# modules/inbound_orbion/services/services_inbound_fotos.py
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models.enums import InboundFotoTipo, ModuleKey, UsageCounterType
from core.models import InboundFoto

from core.services.services_entitlements import resolve_entitlements
from core.services.services_usage import get_usage_value, increment_usage_dual

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,  # puede ser (recepcion) o (db, negocio_id, recepcion_id) según baseline
)

# =========================================================
# Storage baseline (MISMO criterio que Documentos)
# =========================================================
STORAGE_ROOT = Path(os.getenv("ORBION_STORAGE_DIR", "./storage")).resolve()

_ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp"}
_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB max por archivo
_READ_CHUNK = 1024 * 1024  # 1MB

_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9._\-\s]")

# Metric key canon para evidencias (documentos + fotos)
_METRIC_EVIDENCIAS_MB = "evidencias_mb"


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "foto"
    name = name.replace("\\", "/").split("/")[-1]  # evita path traversal
    name = _FILENAME_SAFE_RE.sub("", name).strip()
    name = re.sub(r"\s+", "_", name)
    return (name[:160] or "foto")


def _validate_image_mime(mime: str | None) -> str:
    mt = (mime or "").lower().strip()
    if not mt:
        raise InboundDomainError("No se pudo detectar el tipo de archivo (content_type).")
    if mt not in _ALLOWED_IMAGE_MIME:
        raise InboundDomainError(f"Tipo de archivo no permitido: {mt}. Permitidos: JPG/PNG/WEBP.")
    return mt


def _ext_from_mime(mime: str, fallback_name: str | None) -> str:
    m = (mime or "").lower().strip()
    if m == "image/jpeg":
        return ".jpg"
    if m == "image/png":
        return ".png"
    if m == "image/webp":
        return ".webp"

    if fallback_name and "." in fallback_name:
        ext = "." + fallback_name.rsplit(".", 1)[-1].lower()
        if 2 <= len(ext) <= 6:
            return ext
    return ""


def _build_path(negocio_id: int, recepcion_id: int, filename: str) -> Path:
    folder = STORAGE_ROOT / "inbound" / "fotos" / f"negocio_{negocio_id}" / f"recepcion_{recepcion_id}"
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
    """
    Reconstruye path absoluto desde uri relativo (DB).
    Defensa traversal: debe quedar dentro de STORAGE_ROOT.
    """
    u = (uri or "").strip().replace("\\", "/")
    if not u:
        raise InboundDomainError("Foto inválida: uri vacía.")
    u = u.lstrip("/")
    abs_path = (STORAGE_ROOT / u).resolve()
    try:
        abs_path.relative_to(STORAGE_ROOT.resolve())
    except Exception:
        raise InboundDomainError("Foto inválida: ruta no permitida.")
    return abs_path


def _try_get_image_dims(raw: bytes) -> tuple[int | None, int | None]:
    """
    Opcional: usa Pillow si está disponible.
    """
    try:
        from PIL import Image  # type: ignore
        from io import BytesIO

        with Image.open(BytesIO(raw)) as im:
            w, h = im.size
            if w and h and w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        pass
    return None, None


def _to_mb(size_bytes: int) -> float:
    try:
        return float(size_bytes) / (1024.0 * 1024.0)
    except Exception:
        return 0.0


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


# =========================================================
# ✅ COMPAT: recepcion editable
# =========================================================
def _recepcion_editable(recepcion, *, db: Session, negocio_id: int, recepcion_id: int) -> bool:
    """
    Compat con ambos patrones:
    - obtener_recepcion_editable(recepcion)
    - obtener_recepcion_editable(db, negocio_id=..., recepcion_id=...)
    """
    try:
        return bool(obtener_recepcion_editable(recepcion))  # type: ignore[misc]
    except TypeError:
        return bool(obtener_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id))
    except Exception:
        # fallback final (no romper): inferimos por estado
        try:
            estado = recepcion.estado.value if recepcion and recepcion.estado else None
        except Exception:
            estado = getattr(recepcion, "estado", None)
        return estado not in ("CERRADO", "CANCELADO")


def _assert_recepcion_editable(db: Session, negocio_id: int, recepcion_id: int):
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
    if not _recepcion_editable(recepcion, db=db, negocio_id=negocio_id, recepcion_id=recepcion_id):
        raise InboundDomainError("La recepción está cerrada/cancelada. No se pueden modificar fotos.")
    return recepcion


def _enforce_evidencias_limit_mb(
    db: Session,
    *,
    negocio,
    negocio_id: int,
    delta_bytes: int,
) -> None:
    """
    Enforce: ent['limits']['inbound']['evidencias_mb'] vs usage BILLABLE evidencias_mb
    """
    ent = resolve_entitlements(negocio)
    inbound_limits = _get_inbound_limits(ent)
    max_mb = _coerce_float(inbound_limits.get("evidencias_mb"))

    if max_mb is None:
        return  # sin límite

    used_mb = get_usage_value(
        db,
        negocio_id,
        ModuleKey.INBOUND,
        _METRIC_EVIDENCIAS_MB,
        counter_type=UsageCounterType.BILLABLE,
    )

    delta_mb = _to_mb(int(delta_bytes))
    if delta_mb <= 0:
        return

    if (float(used_mb) + float(delta_mb)) > float(max_mb) + 1e-9:
        remaining = max(0.0, float(max_mb) - float(used_mb))
        raise InboundDomainError(
            f"Límite de evidencias excedido. Disponible: {remaining:.2f} MB (de {float(max_mb):.0f} MB)."
        )


async def _save_upload_streaming_image(
    file: UploadFile,
    dest: Path,
) -> tuple[int, str, bytes]:
    """
    Guarda UploadFile en streaming:
    - valida tamaño máximo por archivo
    - calcula sha256
    - retorna (size_bytes, sha256_hex, raw_bytes) para dims opcional (baseline simple)
    """
    hasher = hashlib.sha256()
    size = 0
    chunks: list[bytes] = []

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
                chunks.append(chunk)

        if size <= 0:
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            raise InboundDomainError("El archivo está vacío.")

        raw = b"".join(chunks)
        return size, hasher.hexdigest(), raw

    except InboundDomainError:
        raise
    except Exception:
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise InboundDomainError("No fue posible guardar la foto. Revisa permisos/storage.")


# =========================================================
# LISTAR / OBTENER
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
        .filter(InboundFoto.negocio_id == int(negocio_id))
        .filter(InboundFoto.recepcion_id == int(recepcion_id))
    )
    if not incluir_inactivas:
        q = q.filter(InboundFoto.activo == 1)

    return q.order_by(InboundFoto.creado_en.desc(), InboundFoto.id.desc()).all()


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
        .filter(InboundFoto.negocio_id == int(negocio_id))
        .filter(InboundFoto.recepcion_id == int(recepcion_id))
        .filter(InboundFoto.id == int(foto_id))
    )
    if not incluir_inactivas:
        q = q.filter(InboundFoto.activo == 1)

    foto = q.first()
    if not foto:
        raise InboundDomainError("Foto no encontrada.")
    return foto


def resolver_foto_storage_path(*, foto: InboundFoto) -> Path:
    return _abs_from_uri(getattr(foto, "storage_relpath", "") or "")


# =========================================================
# CREAR (UPLOAD) + USAGE + LIMITS
# =========================================================

async def crear_foto_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    file: UploadFile,
    tipo: InboundFotoTipo = InboundFotoTipo.GENERAL,
    titulo: Optional[str] = None,
    nota: Optional[str] = None,
    incidencia_id: Optional[int] = None,
    is_principal: bool = False,
    creado_por: Optional[str] = None,
    negocio=None,  # ✅ pasar Negocio desde ruta para enforcement sin query extra
) -> InboundFoto:
    if file is None or not file.filename:
        raise InboundDomainError("Debes seleccionar un archivo.")

    # ✅ guard editable + tenant
    _ = _assert_recepcion_editable(db, negocio_id, recepcion_id)

    # ✅ valida mime
    content_type = _validate_image_mime(file.content_type)
    ext = _ext_from_mime(content_type, file.filename)

    # ✅ enforcement conservador antes de guardar (evita escribir si está al límite)
    if negocio is not None:
        _enforce_evidencias_limit_mb(db, negocio=negocio, negocio_id=negocio_id, delta_bytes=_MAX_SIZE_BYTES)

    original = _safe_filename(file.filename or "foto")
    dest = _build_path(negocio_id, recepcion_id, f"{original}{ext}")

    size_bytes, sha256_hex, raw = await _save_upload_streaming_image(file, dest)

    # dims opcional
    width_px, height_px = _try_get_image_dims(raw)

    # ✅ enforcement real con size final
    if negocio is not None:
        _enforce_evidencias_limit_mb(db, negocio=negocio, negocio_id=negocio_id, delta_bytes=size_bytes)

    storage_relpath = _rel_uri(dest)

    foto = InboundFoto(
        negocio_id=int(negocio_id),
        recepcion_id=int(recepcion_id),
        incidencia_id=int(incidencia_id) if incidencia_id else None,
        tipo=tipo,
        titulo=(titulo or "").strip() or None,
        nota=(nota or "").strip() or None,
        filename_original=(file.filename or "foto").strip() or "foto",
        storage_relpath=storage_relpath,
        content_type=content_type,
        size_bytes=int(size_bytes),
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

    # ✅ usage dual: BILLABLE + OPERATIONAL (Strategy C)
    delta_mb = _to_mb(int(size_bytes))
    if delta_mb > 0:
        try:
            increment_usage_dual(
                db,
                int(negocio_id),
                ModuleKey.INBOUND,
                _METRIC_EVIDENCIAS_MB,
                delta=float(delta_mb),
            )
        except Exception:
            # resiliente: no romper por contadores
            pass

    return foto


# =========================================================
# ELIMINAR (SOFT) + USAGE
# =========================================================

def eliminar_foto_soft(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    foto_id: int,
    eliminado_por: Optional[str] = None,
    motivo_eliminacion: Optional[str] = None,
) -> InboundFoto:
    # ✅ guard editable + tenant
    _ = _assert_recepcion_editable(db, negocio_id, recepcion_id)

    foto = obtener_foto_segura(
        db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        foto_id=foto_id,
        incluir_inactivas=True,
    )

    if int(getattr(foto, "activo", 1) or 0) == 0:
        return foto  # idempotente

    foto.activo = 0
    foto.eliminado_por = (eliminado_por or "").strip() or None
    foto.eliminado_en = utcnow()
    foto.motivo_eliminacion = (motivo_eliminacion or "").strip() or None

    db.add(foto)
    db.flush()

    # Billing/usage del mes NO se decrementa en baseline (Strategy C).
    return foto
