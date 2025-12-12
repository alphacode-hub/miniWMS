# modules/inbound_orbion/routes/routes_inbound_documentos.py

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, InboundDocumento

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_documentos import (
    crear_documento_inbound,
    marcar_documento_validado,
    eliminar_documento_inbound,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================
#   LISTA DE DOCUMENTOS
# ============================

@router.get("/{recepcion_id}/documentos", response_class=HTMLResponse)
async def inbound_documentos_lista(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    # Validar que la recepción exista y pertenezca al negocio
    try:
        recepcion = obtener_recepcion_segura(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "documentos_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    documentos = (
        db.query(InboundDocumento)
        .filter(
            InboundDocumento.negocio_id == negocio_id,
            InboundDocumento.recepcion_id == recepcion_id,
        )
        .order_by(InboundDocumento.subido_en.desc())
        .all()
    )

    log_inbound_event(
        "documentos_lista_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        total_documentos=len(documentos),
    )

    return templates.TemplateResponse(
        "inbound_documentos.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "documentos": documentos,
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   NUEVO DOCUMENTO
# ============================

@router.post("/{recepcion_id}/documentos/nuevo", response_class=HTMLResponse)
async def inbound_documentos_nuevo(
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    tipo: str = Form(...),
    nombre_archivo: str = Form(...),
    ruta_archivo: str = Form(...),
    mime_type: str = Form(""),
    es_obligatorio: str = Form("false"),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]

    # Normalizar flag obligatorio
    flag_obligatorio = es_obligatorio.lower() in ("true", "on", "1", "si", "sí")

    try:
        doc = crear_documento_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            nombre_archivo=nombre_archivo,
            ruta_archivo=ruta_archivo,
            mime_type=mime_type,
            es_obligatorio=flag_obligatorio,
            observaciones=observaciones,
            subido_por_id=user["id"],
        )
    except InboundDomainError as e:
        log_inbound_error(
            "documento_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "documento_agregado",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        documento_id=doc.id,
        tipo=doc.tipo,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}/documentos",
        status_code=302,
    )


# ============================
#   VALIDAR DOCUMENTO
# ============================

@router.post("/{recepcion_id}/documentos/{documento_id}/validar", response_class=HTMLResponse)
async def inbound_documentos_validar(
    recepcion_id: int,
    documento_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    marcar_valido: str = Form("true"),
):
    negocio_id = user["negocio_id"]

    flag_valido = marcar_valido.lower() in ("true", "on", "1", "si", "sí")

    try:
        doc = marcar_documento_validado(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            documento_id=documento_id,
            es_valido=flag_valido,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "documento_validar_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            documento_id=documento_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "documento_validado",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        documento_id=doc.id,
        es_validado=doc.es_validado,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}/documentos",
        status_code=302,
    )


# ============================
#   ELIMINAR DOCUMENTO
# ============================

@router.post("/{recepcion_id}/documentos/{documento_id}/eliminar", response_class=HTMLResponse)
async def inbound_documentos_eliminar(
    recepcion_id: int,
    documento_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        eliminar_documento_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            documento_id=documento_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "documento_eliminar_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            documento_id=documento_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "documento_eliminado",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        documento_id=documento_id,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}/documentos",
        status_code=302,
    )
