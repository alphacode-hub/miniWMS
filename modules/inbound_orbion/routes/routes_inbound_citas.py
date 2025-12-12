# modules/inbound_orbion/routes/routes_inbound_citas.py

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundCita, InboundRecepcion

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from modules.inbound_orbion.services.services_inbound_citas import (
    crear_cita_inbound,
    actualizar_cita_inbound,
    marcar_llegada_cita,
    vincular_cita_a_recepcion,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ============================
#   LISTA DE CITAS
# ============================

@router.get("/citas", response_class=HTMLResponse)
async def inbound_citas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    estado: Optional[str] = None,
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    q = db.query(InboundCita).filter(InboundCita.negocio_id == negocio_id)

    if estado:
        q = q.filter(InboundCita.estado == estado)

    citas = q.order_by(InboundCita.fecha_hora_cita.asc()).all()

    log_inbound_event(
        "citas_lista_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        total_citas=len(citas),
        estado=estado,
    )

    return templates.TemplateResponse(
        "inbound_citas_lista.html",
        {
            "request": request,
            "user": user,
            "citas": citas,
            "estado_filtro": estado or "",
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   NUEVA CITA (FORM)
# ============================

@router.get("/citas/nueva", response_class=HTMLResponse)
async def inbound_citas_nueva_form(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    recepcion_id: Optional[int] = None,
):
    negocio_id = user["negocio_id"]

    recepcion = None
    if recepcion_id:
        recepcion = (
            db.query(InboundRecepcion)
            .filter(
                InboundRecepcion.id == recepcion_id,
                InboundRecepcion.negocio_id == negocio_id,
            )
            .first()
        )

    return templates.TemplateResponse(
        "inbound_citas_form.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   NUEVA CITA (SUBMIT)
# ============================

@router.post("/citas/nueva", response_class=HTMLResponse)
async def inbound_citas_nueva_submit(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    proveedor: str = Form(""),
    transportista: str = Form(""),
    patente_camion: str = Form(""),
    nombre_conductor: str = Form(""),
    fecha_hora_cita: str = Form(...),
    observaciones: str = Form(""),
    recepcion_id: Optional[int] = Form(None),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    # Parse fecha/hora
    try:
        dt_cita = datetime.fromisoformat(fecha_hora_cita)
    except ValueError as e:
        log_inbound_error(
            "cita_fecha_invalida",
            negocio_id=negocio_id,
            user_email=user["email"],
            raw_value=fecha_hora_cita,
            error=str(e),
        )
        raise HTTPException(
            status_code=400,
            detail="Fecha y hora de cita inválida (usar formato ISO).",
        )

    try:
        # 1) Crear cita con service
        cita = crear_cita_inbound(
            db=db,
            negocio_id=negocio_id,
            proveedor=proveedor,
            transportista=transportista,
            patente_camion=patente_camion,
            nombre_conductor=nombre_conductor,
            fecha_hora_cita=dt_cita,
            observaciones=observaciones,
        )

        # 2) Opcional: vincular a recepción si viene recepcion_id
        recepcion_vinculada_id = None
        if recepcion_id:
            cita = vincular_cita_a_recepcion(
                db=db,
                negocio_id=negocio_id,
                cita_id=cita.id,
                recepcion_id=recepcion_id,
            )
            recepcion_vinculada_id = recepcion_id

    except InboundDomainError as e:
        log_inbound_error(
            "cita_creada_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    # Logging de éxito
    log_inbound_event(
        "cita_creada",
        negocio_id=negocio_id,
        user_email=user["email"],
        cita_id=cita.id,
        recepcion_id=recepcion_vinculada_id,
    )

    return RedirectResponse(
        url="/inbound/citas",
        status_code=302,
    )


# ============================
#   CAMBIO DE ESTADO CITA
# ============================

@router.post("/citas/{cita_id}/estado", response_class=HTMLResponse)
async def inbound_citas_cambiar_estado(
    cita_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    nuevo_estado: str = Form(...),
):
    negocio_id = user["negocio_id"]

    try:
        # Si pasa a ARRIBADO, usamos service específico (setea llegada real)
        if nuevo_estado == "ARRIBADO":
            cita = marcar_llegada_cita(
                db=db,
                negocio_id=negocio_id,
                cita_id=cita_id,
            )
        else:
            cita = actualizar_cita_inbound(
                db=db,
                negocio_id=negocio_id,
                cita_id=cita_id,
                estado=nuevo_estado,
            )

    except InboundDomainError as e:
        log_inbound_error(
            "cita_cambio_estado_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            cita_id=cita_id,
            nuevo_estado=nuevo_estado,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "cita_cambio_estado",
        negocio_id=negocio_id,
        user_email=user["email"],
        cita_id=cita.id,
        estado_anterior=None,   # si quieres puedes leerlo antes con un SELECT
        estado_nuevo=cita.estado,
    )

    return RedirectResponse(
        url="/inbound/citas",
        status_code=302,
    )
