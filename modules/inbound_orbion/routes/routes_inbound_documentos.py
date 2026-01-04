# modules/inbound_orbion/routes/routes_inbound_documentos.py
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,  # ✅ firma baseline: (db, negocio_id, recepcion_id)
)

from modules.inbound_orbion.services.services_inbound_documentos import (
    listar_documentos,
    crear_documento,
    obtener_documento,
    eliminar_documento,
    STORAGE_ROOT,
)

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

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


def _is_recepcion_editable(db: Session, *, negocio_id: int, recepcion_id: int) -> bool:
    try:
        return bool(obtener_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id))
    except Exception:
        return False


def _abs_doc_path(uri: str) -> Path:
    """
    DB guarda uri relativo (ej: inbound/negocio_1/recepcion_10/xxx.pdf)
    => reconstruimos path absoluto usando STORAGE_ROOT.
    Defensa traversal: debe quedar dentro de STORAGE_ROOT.
    """
    u = (uri or "").strip().replace("\\", "/")
    if not u:
        raise InboundDomainError("Documento inválido: uri vacía.")

    u = u.lstrip("/")
    abs_path = (Path(STORAGE_ROOT) / u).resolve()

    try:
        abs_path.relative_to(Path(STORAGE_ROOT).resolve())
    except Exception:
        raise InboundDomainError("Documento inválido: ruta no permitida.")

    return abs_path


# =========================================================
# VISTA
# =========================================================

@router.get("/recepciones/{recepcion_id}/documentos", response_class=HTMLResponse)
async def inbound_documentos_view(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)
    neg = get_negocio_or_404(db, negocio_id)

    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        recepcion_editable = _is_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        docs = listar_documentos(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        log_inbound_event(
            "documentos_view",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            total=len(docs),
            editable=int(1 if recepcion_editable else 0),
        )

        return templates.TemplateResponse(
            "inbound_documentos.html",
            {
                "request": request,
                "user": user,
                "negocio": neg,
                "recepcion": recepcion,
                "recepcion_editable": recepcion_editable,
                "documentos": docs,
                "ok": ok,
                "error": error,
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        log_inbound_error(
            "documentos_view_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            error=e,
        )

        return templates.TemplateResponse(
            "inbound_documentos.html",
            {
                "request": request,
                "user": user,
                "negocio": neg,
                "recepcion": None,
                "recepcion_editable": False,
                "documentos": [],
                "ok": None,
                "error": str(e),
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=200,
        )


# =========================================================
# SUBIR
# =========================================================

@router.post("/recepciones/{recepcion_id}/documentos", response_class=HTMLResponse)
async def inbound_documentos_upload(
    request: Request,
    recepcion_id: int,
    tipo: str = Form(...),
    descripcion: str | None = Form(None),
    linea_id: int | None = Form(None),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)
    neg = get_negocio_or_404(db, negocio_id)

    committed = False
    created_doc_id: int | None = None

    try:
        if archivo is None or not archivo.filename:
            raise InboundDomainError("Debes adjuntar un archivo.")

        _ = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        if not _is_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id):
            raise InboundDomainError("La recepción está cerrada. No puedes subir documentos.")

        doc = await crear_documento(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            descripcion=descripcion,
            file=archivo,
            creado_por=email,
            linea_id=linea_id,
            negocio=neg,
        )

        db.commit()
        committed = True
        created_doc_id = int(getattr(doc, "id", 0) or 0) or None

        log_inbound_event(
            "documento_subido",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            tipo=str(tipo),
            documento_id=int(created_doc_id or 0),
            size_bytes=int(getattr(doc, "size_bytes", 0) or 0),
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", ok="Documento subido.")

    except InboundDomainError as e:
        if not committed:
            db.rollback()

        log_inbound_error(
            "documento_upload_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(created_doc_id or 0),
            error=e,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error=str(e))

    except Exception as exc:
        if not committed:
            db.rollback()
            log_inbound_error(
                "documento_upload_unexpected",
                negocio_id=negocio_id,
                recepcion_id=int(recepcion_id),
                user_email=email,
                documento_id=int(created_doc_id or 0),
                error=exc,
            )
            return _redirect(
                f"/inbound/recepciones/{recepcion_id}/documentos",
                error="Error inesperado al subir documento. Revisa logs.",
            )

        log_inbound_error(
            "documento_upload_postcommit_warning",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(created_doc_id or 0),
            error=exc,
        )
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/documentos",
            ok="Documento subido.",
        )


# =========================================================
# DESCARGAR
# =========================================================

@router.get("/recepciones/{recepcion_id}/documentos/{documento_id}/download")
async def inbound_documentos_download(
    recepcion_id: int,
    documento_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)
    _ = get_negocio_or_404(db, negocio_id)

    try:
        doc = obtener_documento(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            documento_id=documento_id,
        )
        abs_path = _abs_doc_path(doc.uri)

        if not abs_path.exists():
            raise InboundDomainError("El archivo no existe en storage. Revisa ORBION_STORAGE_DIR.")

        log_inbound_event(
            "documento_download",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(documento_id),
        )

        return FileResponse(
            path=str(abs_path),
            media_type=doc.mime_type or "application/octet-stream",
            filename=doc.nombre,
        )

    except InboundDomainError as e:
        log_inbound_error(
            "documento_download_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(documento_id),
            error=e,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error=str(e))

    except Exception as exc:
        log_inbound_error(
            "documento_download_unexpected",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(documento_id),
            error=exc,
        )
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
    email = _get_user_email(user)
    _ = get_negocio_or_404(db, negocio_id)

    try:
        _ = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        if not _is_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id):
            raise InboundDomainError("La recepción está cerrada. No puedes eliminar documentos.")

        eliminar_documento(db, negocio_id=negocio_id, recepcion_id=recepcion_id, documento_id=documento_id)
        db.commit()

        log_inbound_event(
            "documento_eliminado",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(documento_id),
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", ok="Documento eliminado.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "documento_delete_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(documento_id),
            error=e,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/documentos", error=str(e))

    except Exception as exc:
        db.rollback()
        log_inbound_error(
            "documento_delete_unexpected",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            documento_id=int(documento_id),
            error=exc,
        )
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/documentos",
            error="Error inesperado al eliminar. Revisa logs.",
        )
