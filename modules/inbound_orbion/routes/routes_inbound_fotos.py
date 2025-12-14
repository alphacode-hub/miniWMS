# modules/inbound_orbion/routes/routes_inbound_fotos.py

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundFoto

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_fotos import (
    crear_foto_inbound,
    eliminar_foto_inbound,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================
#   LISTA DE FOTOS
# ============================

@router.get("/recepciones/{recepcion_id}/fotos", response_class=HTMLResponse)
async def inbound_fotos_lista(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        recepcion = obtener_recepcion_segura(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "fotos_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    fotos = (
        db.query(InboundFoto)
        .filter(
            InboundFoto.negocio_id == negocio_id,
            InboundFoto.recepcion_id == recepcion_id,
        )
        .order_by(InboundFoto.subido_en.desc())
        .all()
    )

    log_inbound_event(
        "fotos_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        total_fotos=len(fotos),
    )

    return templates.TemplateResponse(
        "inbound_fotos.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "fotos": fotos,
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   NUEVA FOTO / EVIDENCIA
# ============================

@router.post("/recepciones/{recepcion_id}/fotos/nueva", response_class=HTMLResponse)
async def inbound_fotos_nueva(
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    tipo: str = Form(""),
    descripcion: str = Form(""),
    ruta_archivo: str = Form(...),
    mime_type: str = Form(""),
):
    negocio_id = user["negocio_id"]

    try:
        foto = crear_foto_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            descripcion=descripcion,
            ruta_archivo=ruta_archivo,
            mime_type=mime_type,
            subido_por_id=user.get("id"),
        )
    except InboundDomainError as e:
        log_inbound_error(
            "foto_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "foto_agregada",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        foto_id=foto.id,
        tipo=foto.tipo,
    )

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/fotos",
        status_code=302,
    )


# ============================
#   ELIMINAR FOTO
# ============================

@router.post("/recepciones/{recepcion_id}/fotos/{foto_id}/eliminar", response_class=HTMLResponse)
async def inbound_fotos_eliminar(
    recepcion_id: int,
    foto_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        eliminar_foto_inbound(
            db=db,
            negocio_id=negocio_id,
            foto_id=foto_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "foto_eliminar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            foto_id=foto_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "foto_eliminada",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        foto_id=foto_id,
    )

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/fotos",
        status_code=302,
    )
