# modules/inbound_orbion/routes/routes_inbound_incidencias.py

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.services.services_audit import registrar_auditoria

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    crear_incidencia_inbound,
    eliminar_incidencia_inbound,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

from .inbound_common import inbound_roles_dep

router = APIRouter()


# ============================
#   INCIDENCIAS
# ============================

@router.post("/recepciones/{recepcion_id}/incidencias", response_class=HTMLResponse)
async def inbound_agregar_incidencia(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    tipo: str = Form(...),
    criticidad: str = Form("media"),
    descripcion: str = Form(...),
):
    negocio_id = user["negocio_id"]

    # Validar recepción (multi-tenant)
    try:
        recepcion = obtener_recepcion_segura(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "incidencia_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user.get("email"),
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    try:
        incidencia = crear_incidencia_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            criticidad=criticidad,
            descripcion=descripcion,
        )

        # Setear creado_por_id si el modelo lo soporta
        if hasattr(incidencia, "creado_por_id"):
            incidencia.creado_por_id = user["id"]
            db.commit()
            db.refresh(incidencia)

    except InboundDomainError as e:
        log_inbound_error(
            "incidencia_crear_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user.get("email"),
            tipo=(tipo or "").strip(),
            criticidad=(criticidad or "").strip(),
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_AGREGAR_INCIDENCIA",
        detalle={
            "inbound_id": recepcion_id,
            "codigo": recepcion.codigo,
            "incidencia_id": incidencia.id,
            "tipo": (tipo or "").strip(),
            "criticidad": (criticidad or "").strip().lower(),
        },
    )

    log_inbound_event(
        "incidencia_creada",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user.get("email"),
        incidencia_id=incidencia.id,
        tipo=(tipo or "").strip(),
        criticidad=(criticidad or "").strip().lower(),
    )

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}",
        status_code=302,
    )


@router.post(
    "/recepciones/{recepcion_id}/incidencias/{incidencia_id}/eliminar",
    response_class=HTMLResponse,
)
async def inbound_eliminar_incidencia(
    recepcion_id: int,
    incidencia_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    # Validar recepción (multi-tenant) para auditar con código
    try:
        recepcion = obtener_recepcion_segura(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "incidencia_delete_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user.get("email"),
            incidencia_id=incidencia_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    try:
        eliminar_incidencia_inbound(
            db=db,
            negocio_id=negocio_id,
            incidencia_id=incidencia_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "incidencia_delete_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user.get("email"),
            incidencia_id=incidencia_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_ELIMINAR_INCIDENCIA",
        detalle={
            "inbound_id": recepcion_id,
            "codigo": recepcion.codigo,
            "incidencia_id": incidencia_id,
        },
    )

    log_inbound_event(
        "incidencia_eliminada",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user.get("email"),
        incidencia_id=incidencia_id,
    )

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}",
        status_code=302,
    )
