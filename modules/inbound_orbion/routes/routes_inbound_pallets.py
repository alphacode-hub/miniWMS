# modules/inbound_orbion/routes/routes_inbound_pallets.py

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundPallet

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_pallets import (
    crear_pallet_inbound,
    eliminar_pallet_inbound,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================
#   LISTA DE PALLETS
# ============================

@router.get("/{recepcion_id}/pallets", response_class=HTMLResponse)
async def inbound_pallets_lista(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    # Validar recepción
    try:
        recepcion = obtener_recepcion_segura(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "pallets_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    pallets = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .order_by(InboundPallet.id.asc())
        .all()
    )

    log_inbound_event(
        "pallets_lista_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        total_pallets=len(pallets),
    )

    return templates.TemplateResponse(
        "inbound_pallets.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallets": pallets,
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   NUEVO PALLET
# ============================

@router.post("/{recepcion_id}/pallets/nuevo", response_class=HTMLResponse)
async def inbound_pallets_nuevo(
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    codigo_pallet: str = Form(...),
    peso_bruto_kg: float | None = Form(None),
    peso_tara_kg: float | None = Form(None),
    bultos: int | None = Form(None),
    temperatura_promedio: float | None = Form(None),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]

    try:
        pallet = crear_pallet_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            codigo_pallet=codigo_pallet,
            peso_bruto_kg=peso_bruto_kg,
            peso_tara_kg=peso_tara_kg,
            bultos=bultos,
            temperatura_promedio=temperatura_promedio,
            observaciones=observaciones,
            creado_por_id=user["id"],
        )
    except InboundDomainError as e:
        log_inbound_error(
            "pallet_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "pallet_creado",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        pallet_id=pallet.id,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}/pallets",
        status_code=302,
    )


# ============================
#   ELIMINAR PALLET
# ============================

@router.post("/{recepcion_id}/pallets/{pallet_id}/eliminar", response_class=HTMLResponse)
async def inbound_pallets_eliminar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        eliminar_pallet_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "pallet_eliminar_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "pallet_eliminado",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}/pallets",
        status_code=302,
    )
