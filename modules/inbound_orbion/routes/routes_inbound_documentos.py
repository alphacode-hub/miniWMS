# modules/inbound_orbion/routes/routes_inbound_documentos.py
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError, obtener_recepcion_segura
from modules.inbound_orbion.services.services_inbound_documentos import (
    listar_documentos,
    crear_documento,
    obtener_documento,
    eliminar_documento,
    STORAGE_ROOT,  # 👈 usamos el mismo root del service
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _get_negocio_id(user) -> int:
    if callable(user):
        user = user()
    if isinstance(user, dict):
        val = user.get("negocio_id")
        if val is None:
            raise InboundDomainError("Sesión inválida: falta negocio_id.")
        return int(val)
    val = getattr(user, "negocio_id", None)
    if val is None:
        raise InboundDomainError("Sesión inválida: falta negocio_id.")
    return int(val)


def _get_user_email(user) -> str | None:
    if callable(user):
        user = user()
    if isinstance(user, dict):
        return user.get("email") or None
    return getattr(user, "email", None) or None


def _abs_doc_path(uri: str) -> Path:
    """
    DB guarda uri relativo (ej: inbound/negocio_1/recepcion_10/xxx.pdf)
    => reconstruimos path absoluto usando STORAGE_ROOT.
    """
    u = (uri or "").strip().replace("\\", "/")
    if not u:
        raise InboundDomainError("Documento inválido: uri vacía.")
    # evita traversal (defensa básica)
    u = u.lstrip("/")
    return (Path(STORAGE_ROOT) / u).resolve()


# =========================================================
# VISTA DOCUMENTOS
# =========================================================

@router.get("/recepciones/{recepcion_id}/documentos", response_class=HTMLResponse)
async def inbound_documentos_view(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        docs = listar_documentos(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        return templates.TemplateResponse(
            "inbound_documentos.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "documentos": docs,
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            },
        )
    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_documentos.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "documentos": [],
                "ok": None,
                "error": str(e),
            },
            status_code=200,
        )


# =========================================================
# SUBIR DOCUMENTO
# =========================================================

@router.post("/recepciones/{recepcion_id}/documentos", response_class=HTMLResponse)
async def inbound_documentos_upload(
    request: Request,
    recepcion_id: int,
    tipo: str = Form(...),
    descripcion: str | None = Form(None),
    # opcional: asociar a línea (si tu UI lo agrega después)
    linea_id: int | None = Form(None),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)

    try:
        if archivo is None or not archivo.filename:
            raise InboundDomainError("Debes adjuntar un archivo.")

        await crear_documento(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            descripcion=descripcion,
            file=archivo,
            creado_por=email,
            linea_id=linea_id,
        )
        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", ok="Documento subido.")
    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error=str(e))
    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/documentos",
            error="Error inesperado al subir documento. Revisa logs.",
        )


# =========================================================
# DESCARGAR / VER
# =========================================================

@router.get("/recepciones/{recepcion_id}/documentos/{documento_id}/download")
async def inbound_documentos_download(
    recepcion_id: int,
    documento_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        doc = obtener_documento(db, negocio_id=negocio_id, recepcion_id=recepcion_id, documento_id=documento_id)
        abs_path = _abs_doc_path(doc.uri)

        if not abs_path.exists():
            raise InboundDomainError("El archivo no existe en storage. Revisa ORBION_STORAGE_DIR.")

        return FileResponse(
            path=str(abs_path),
            media_type=doc.mime_type or "application/octet-stream",
            filename=doc.nombre,
        )
    except InboundDomainError as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error=str(e))
    except Exception:
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/documentos",
            error="Error inesperado al descargar documento. Revisa logs.",
        )


# =========================================================
# ELIMINAR (soft delete)
# =========================================================

@router.post("/recepciones/{recepcion_id}/documentos/{documento_id}/eliminar", response_class=HTMLResponse)
async def inbound_documentos_delete(
    request: Request,
    recepcion_id: int,
    documento_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    try:
        eliminar_documento(db, negocio_id=negocio_id, recepcion_id=recepcion_id, documento_id=documento_id)
        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", ok="Documento eliminado.")
    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error=str(e))
    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error="Error inesperado al eliminar. Revisa logs.")
