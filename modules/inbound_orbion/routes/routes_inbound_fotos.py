from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models.enums import RecepcionEstado, InboundFotoTipo

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,
)

from modules.inbound_orbion.services.services_inbound_fotos import (
    listar_fotos_recepcion,
    crear_foto_recepcion,
    eliminar_foto_soft,
    obtener_foto_segura,
    resolver_foto_storage_path,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


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
        return user.get("email")
    return getattr(user, "email", None)


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if ok:
        url = f"{url}{sep}ok={ok}"
        sep = "&"
    if error:
        url = f"{url}{sep}error={error}"
    return RedirectResponse(url=url, status_code=303)


def _parse_tipo(tipo: str | None) -> InboundFotoTipo:
    if not tipo:
        return InboundFotoTipo.GENERAL
    try:
        return InboundFotoTipo(str(tipo).strip().upper())
    except Exception:
        raise InboundDomainError("Tipo de foto inválido.")


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
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        # ✅ Baseline real: obtener_recepcion_editable(db, negocio_id, recepcion_id)
        recepcion_editable = bool(obtener_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id))
        es_cerrada = not recepcion_editable

        fotos = listar_fotos_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        return templates.TemplateResponse(
            "inbound_fotos.html",
            {
                "request": request,
                "user": user,
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
        return templates.TemplateResponse(
            "inbound_fotos.html",
            {
                "request": request,
                "user": user,
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
    creado_por = _get_user_email(user)

    try:
        obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        # ✅ Baseline real: editable se consulta por ids
        if not bool(obtener_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id)):
            raise InboundDomainError("La recepción está cerrada. No puedes subir fotos.")

        await crear_foto_recepcion(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            file=archivo,
            titulo=titulo,
            nota=nota,
            tipo=_parse_tipo(tipo),
            incidencia_id=incidencia_id,
            is_principal=bool(is_principal),
            creado_por=creado_por,
        )
        db.commit()
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            ok="Foto subida.",
        )

    except InboundDomainError as e:
        db.rollback()
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error=str(e),
        )

    except Exception:
        db.rollback()
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error="Error inesperado al subir foto. Revisa logs.",
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

    try:
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

        return FileResponse(
            path=str(abs_path),
            media_type=foto.content_type or "application/octet-stream",
            filename=foto.filename_original or f"foto_{foto.id}",
        )

    except InboundDomainError as e:
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error=str(e),
        )

    except Exception:
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
    eliminado_por = _get_user_email(user)

    try:
        obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        if not bool(obtener_recepcion_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id)):
            raise InboundDomainError("La recepción está cerrada. No puedes eliminar fotos.")

        eliminar_foto_soft(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            foto_id=foto_id,
            eliminado_por=eliminado_por,
            motivo_eliminacion=motivo,
        )
        db.commit()
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            ok="Foto eliminada.",
        )

    except InboundDomainError as e:
        db.rollback()
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error=str(e),
        )

    except Exception:
        db.rollback()
        return _redirect(
            str(request.url_for("inbound_fotos_recepcion", recepcion_id=recepcion_id)),
            error="Error inesperado al eliminar foto. Revisa logs.",
        )
