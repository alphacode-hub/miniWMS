# modules/inbound_orbion/routes/routes_inbound_citas.py
"""
Rutas Citas – Inbound ORBION

✔ Lista, creación, cambio de estado y vinculación con recepciones
✔ Multi-tenant estricto
✔ Validación y normalización en capa de rutas
✔ Dominio encapsulado en services_inbound_citas
✔ Logging estructurado enterprise
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundCita, InboundRecepcion

from modules.inbound_orbion.services.services_inbound import InboundDomainError
from modules.inbound_orbion.services.services_inbound_citas import (
    crear_cita_inbound,
    actualizar_cita_inbound,
    marcar_llegada_cita,
    vincular_cita_a_recepcion,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

from .inbound_common import inbound_roles_dep, get_negocio_or_404, templates

router = APIRouter()


# ============================
#   HELPERS
# ============================

def _parse_datetime_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        raise InboundDomainError(
            "Fecha y hora inválida. Usa formato ISO (ej: 2025-12-12T14:30)."
        )


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
        user_email=user.get("email"),
        total_citas=len(citas),
        estado_filtro=estado,
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

    try:
        dt_cita = _parse_datetime_iso(fecha_hora_cita)

        cita = crear_cita_inbound(
            db=db,
            negocio_id=negocio_id,
            proveedor=proveedor or None,
            transportista=transportista or None,
            patente_camion=patente_camion or None,
            nombre_conductor=nombre_conductor or None,
            fecha_hora_cita=dt_cita,
            observaciones=observaciones or None,
        )

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
            "cita_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=400, detail=getattr(e, "message", str(e)))

    log_inbound_event(
        "cita_creada",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        cita_id=cita.id,
        recepcion_id=recepcion_vinculada_id,
    )

    return RedirectResponse(url="/inbound/citas", status_code=302)


# ============================
#   CAMBIO DE ESTADO
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
            "cita_cambio_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita_id,
            estado=nuevo_estado,
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=400, detail=getattr(e, "message", str(e)))

    log_inbound_event(
        "cita_estado_actualizado",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        cita_id=cita.id,
        estado_nuevo=cita.estado,
    )

    return RedirectResponse(url="/inbound/citas", status_code=302)
