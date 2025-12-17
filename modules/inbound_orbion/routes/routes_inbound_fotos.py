# modules/inbound_orbion/routes/routes_inbound_fotos.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

from modules.inbound_orbion.services.services_inbound_fotos import (
    listar_fotos_recepcion,
    crear_foto_recepcion,
    eliminar_foto,
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
        fotos = listar_fotos_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        return templates.TemplateResponse(
            "inbound_fotos_recepcion.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "fotos": fotos,
                "ok": ok,
                "error": error,
            },
        )

    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_fotos_recepcion.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "fotos": [],
                "ok": None,
                "error": str(e),
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
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        await crear_foto_recepcion(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            file=archivo,
            titulo=titulo,
            nota=nota,
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
# ELIMINAR
# =========================================================

@router.post("/recepciones/{recepcion_id}/fotos/{foto_id}/eliminar", response_class=HTMLResponse)
async def inbound_fotos_eliminar(
    request: Request,
    recepcion_id: int,
    foto_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        eliminar_foto(db, negocio_id=negocio_id, recepcion_id=recepcion_id, foto_id=foto_id)
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
