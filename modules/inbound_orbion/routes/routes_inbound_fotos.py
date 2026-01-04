# modules/inbound_orbion/routes/routes_inbound_fotos.py
from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models.enums import RecepcionEstado, InboundFotoTipo

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,  # ✅ firma: (db, negocio_id, recepcion_id)
)

from modules.inbound_orbion.services.services_inbound_fotos import (
    listar_fotos_recepcion,
    crear_foto_recepcion,
    eliminar_foto_soft,
    obtener_foto_segura,
    resolver_foto_storage_path,
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


def _parse_tipo(tipo: str | None) -> InboundFotoTipo:
    t = (tipo or "").strip().upper()
    if not t:
        return InboundFotoTipo.GENERAL

    try:
        return InboundFotoTipo[t]  # type: ignore[index]
    except Exception:
        pass

    try:
        return InboundFotoTipo(t)
    except Exception:
        return InboundFotoTipo.GENERAL


def _is_recepcion_editable(db: Session, *, negocio_id: int, recepcion_id: int) -> bool:
    try:
        return bool(obtener_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id))
    except Exception:
        return False


def _validate_upload_file(archivo: UploadFile | None) -> None:
    if archivo is None or not archivo.filename:
        raise InboundDomainError("Debes adjuntar un archivo.")

    ct = (archivo.content_type or "").lower().strip()
    if ct and not ct.startswith("image/"):
        raise InboundDomainError("Formato no permitido. Sube una imagen (JPG/PNG/WEBP).")


# =========================================================
# VISTA
# =========================================================

@router.get("/recepciones/{recepcion_id}/fotos", response_class=HTMLResponse)
async def inbound_fotos_recepcion(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)

    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    neg = get_negocio_or_404(db, negocio_id)

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        recepcion_editable = _is_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        es_cerrada = not recepcion_editable

        fotos = listar_fotos_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        log_inbound_event(
            "fotos_view",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            total=len(fotos),
            editable=int(1 if recepcion_editable else 0),
        )

        return templates.TemplateResponse(
            "inbound_fotos.html",
            {
                "request": request,
                "user": user,
                "negocio": neg,
                "recepcion": recepcion,
                "recepcion_editable": recepcion_editable,
                "es_cerrada": es_cerrada,
                "fotos": fotos,
                "ok": ok,
                "error": error,
                "InboundFotoTipo": InboundFotoTipo,
                "RecepcionEstado": RecepcionEstado,
            },
        )

    except InboundDomainError as e:
        log_inbound_error(
            "fotos_view_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            error=e,
        )

        return templates.TemplateResponse(
            "inbound_fotos.html",
            {
                "request": request,
                "user": user,
                "negocio": neg,
                "recepcion": None,
                "recepcion_editable": False,
                "es_cerrada": True,
                "fotos": [],
                "ok": None,
                "error": str(e),
                "InboundFotoTipo": InboundFotoTipo,
                "RecepcionEstado": RecepcionEstado,
            },
            status_code=200,
        )


# =========================================================
# SUBIR
# =========================================================

@router.post("/recepciones/{recepcion_id}/fotos/subir", response_class=HTMLResponse)
async def inbound_fotos_subir(
    request: Request,
    recepcion_id: int,
    titulo: str | None = Form(None),
    nota: str | None = Form(None),
    tipo: str | None = Form(None),
    incidencia_id: int | None = Form(None),
    is_principal: int | None = Form(None),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)
    neg = get_negocio_or_404(db, negocio_id)

    committed = False
    created_foto_id: int | None = None

    try:
        _validate_upload_file(archivo)

        _ = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        if not _is_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id):
            raise InboundDomainError("La recepción está cerrada. No puedes subir fotos.")

        foto = await crear_foto_recepcion(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            file=archivo,
            titulo=titulo,
            nota=nota,
            tipo=_parse_tipo(tipo),
            incidencia_id=incidencia_id,
            is_principal=bool(is_principal),
            creado_por=email,
            negocio=neg,
        )

        db.commit()
        committed = True
        created_foto_id = int(getattr(foto, "id", 0) or 0) or None

        log_inbound_event(
            "foto_subida",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            tipo=str(tipo or ""),
            foto_id=int(created_foto_id or 0),
            size_bytes=int(getattr(foto, "size_bytes", 0) or 0),
            filename=str(getattr(foto, "filename_original", "") or ""),
        )

        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            ok="Foto subida.",
        )

    except InboundDomainError as e:
        if not committed:
            db.rollback()

        log_inbound_error(
            "foto_upload_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(created_foto_id or 0),
            error=e,
        )

        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error=str(e),
        )

    except Exception as exc:
        if not committed:
            db.rollback()
            log_inbound_error(
                "foto_upload_unexpected",
                negocio_id=negocio_id,
                recepcion_id=int(recepcion_id),
                user_email=email,
                foto_id=int(created_foto_id or 0),
                error=exc,
            )
            return _redirect(
                str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
                error="Error inesperado al subir foto. Revisa logs.",
            )

        log_inbound_error(
            "foto_upload_postcommit_warning",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(created_foto_id or 0),
            error=exc,
        )
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            ok="Foto subida.",
        )


# =========================================================
# VER / DESCARGAR
# =========================================================

@router.get("/recepciones/{recepcion_id}/fotos/{foto_id}/archivo", response_class=HTMLResponse)
async def inbound_fotos_archivo(
    request: Request,
    recepcion_id: int,
    foto_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)

    try:
        _ = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        foto = obtener_foto_segura(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            foto_id=foto_id,
            incluir_inactivas=False,
        )

        abs_path = resolver_foto_storage_path(foto=foto)
        if not abs_path.exists() or not abs_path.is_file():
            raise InboundDomainError("Archivo no encontrado en storage.")

        log_inbound_event(
            "foto_archivo",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(foto_id),
        )

        return FileResponse(
            path=str(abs_path),
            media_type=foto.content_type or "application/octet-stream",
            filename=foto.filename_original or f"foto_{foto.id}",
        )

    except InboundDomainError as e:
        log_inbound_error(
            "foto_archivo_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(foto_id),
            error=e,
        )
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error=str(e),
        )

    except Exception as exc:
        log_inbound_error(
            "foto_archivo_unexpected",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(foto_id),
            error=exc,
        )
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error="Error inesperado al abrir la foto. Revisa logs.",
        )


# =========================================================
# ELIMINAR (SOFT)
# =========================================================

@router.post("/recepciones/{recepcion_id}/fotos/{foto_id}/eliminar", response_class=HTMLResponse)
async def inbound_fotos_eliminar(
    request: Request,
    recepcion_id: int,
    foto_id: int,
    motivo: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    email = _get_user_email(user)

    try:
        _ = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        if not _is_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id):
            raise InboundDomainError("La recepción está cerrada. No puedes eliminar fotos.")

        eliminar_foto_soft(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            foto_id=foto_id,
            eliminado_por=email,
            motivo_eliminacion=motivo,
        )
        db.commit()

        log_inbound_event(
            "foto_eliminada",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(foto_id),
            motivo=str(motivo or ""),
        )

        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            ok="Foto eliminada.",
        )

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "foto_delete_error",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(foto_id),
            error=e,
        )
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error=str(e),
        )

    except Exception as exc:
        db.rollback()
        log_inbound_error(
            "foto_delete_unexpected",
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            user_email=email,
            foto_id=int(foto_id),
            error=exc,
        )
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error="Error inesperado al eliminar foto. Revisa logs.",
        )
