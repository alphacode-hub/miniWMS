# modules/inbound_orbion/routes/routes_inbound_incidencias.py

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.services.services_audit import registrar_auditoria

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    crear_incidencia_inbound,
    eliminar_incidencia_inbound,
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

@router.post("/{recepcion_id}/incidencias", response_class=HTMLResponse)
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

    try:
        incidencia = crear_incidencia_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            criticidad=criticidad,
            descripcion=descripcion,
        )
        incidencia.creado_por_id = user["id"]
        db.commit()
    except InboundDomainError as e:
        log_inbound_error(
            "agregar_incidencia_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            tipo=tipo,
            criticidad=criticidad,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_AGREGAR_INCIDENCIA",
        detalle={
            "inbound_id": recepcion_id,
            "incidencia_id": incidencia.id,
            "tipo": tipo,
            "criticidad": criticidad,
        },
    )

    log_inbound_event(
        "agregar_incidencia",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        incidencia_id=incidencia.id,
        tipo=tipo,
        criticidad=criticidad,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )


@router.post("/{recepcion_id}/incidencias/{incidencia_id}/eliminar", response_class=HTMLResponse)
async def inbound_eliminar_incidencia(
    recepcion_id: int,
    incidencia_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        eliminar_incidencia_inbound(
            db=db,
            negocio_id=negocio_id,
            incidencia_id=incidencia_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "eliminar_incidencia_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
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
            "incidencia_id": incidencia_id,
        },
    )

    log_inbound_event(
        "eliminar_incidencia",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        incidencia_id=incidencia_id,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )
