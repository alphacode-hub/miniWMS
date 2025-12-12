# modules/inbound_orbion/routes/routes_inbound_checklist.py

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import (
    InboundChecklistItem,
    InboundChecklistRespuesta,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ============================
#   CONFIGURACIÓN CHECKLIST
# ============================

@router.get("/checklist/config", response_class=HTMLResponse)
async def inbound_checklist_config(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    items = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
        )
        .order_by(InboundChecklistItem.orden.asc())
        .all()
    )

    log_inbound_event(
        "checklist_config_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        total_items=len(items),
    )

    return templates.TemplateResponse(
        "inbound_checklist_config.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/checklist/config/nuevo", response_class=HTMLResponse)
async def inbound_checklist_config_nuevo(
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    texto: str = Form(...),
    orden: int = Form(1),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    item = InboundChecklistItem(
        negocio_id=negocio_id,
        texto=texto.strip(),
        orden=orden,
        activo=True,
    )

    db.add(item)
    db.commit()

    log_inbound_event(
        "checklist_item_creado",
        negocio_id=negocio_id,
        user_email=user["email"],
        item_id=item.id,
    )

    return RedirectResponse(
        url="/inbound/checklist/config",
        status_code=302,
    )


@router.post("/checklist/config/{item_id}/toggle", response_class=HTMLResponse)
async def inbound_checklist_config_toggle(
    item_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    item = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.id == item_id,
            InboundChecklistItem.negocio_id == negocio_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item de checklist no encontrado")

    item.activo = not item.activo
    db.commit()

    log_inbound_event(
        "checklist_item_toggle",
        negocio_id=negocio_id,
        user_email=user["email"],
        item_id=item.id,
        activo=item.activo,
    )

    return RedirectResponse(
        url="/inbound/checklist/config",
        status_code=302,
    )


# ============================
#   RESPUESTA CHECKLIST
# ============================

@router.get("/{recepcion_id}/checklist", response_class=HTMLResponse)
async def inbound_checklist_responder_view(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    # Usamos helper de dominio para validar recepción + negocio
    try:
        recepcion = obtener_recepcion_segura(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "checklist_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    items = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
            InboundChecklistItem.activo == True,
        )
        .order_by(InboundChecklistItem.orden.asc())
        .all()
    )

    respuestas = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
        )
        .all()
    )
    respuestas_dict = {r.item_id: r for r in respuestas}

    log_inbound_event(
        "checklist_responder_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        total_items=len(items),
        total_respuestas=len(respuestas),
    )

    return templates.TemplateResponse(
        "inbound_checklist_responder.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "items": items,
            "respuestas_dict": respuestas_dict,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/{recepcion_id}/checklist/guardar", response_class=HTMLResponse)
async def inbound_checklist_guardar(
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
            "checklist_guardar_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    form = await request.form()

    items = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
            InboundChecklistItem.activo == True,
        )
        .all()
    )
    items_dict = {i.id: i for i in items}

    existing = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
        )
        .all()
    )
    existing_dict = {r.item_id: r for r in existing}

    for item_id, item in items_dict.items():
        key_bool = f"item_{item_id}_valor"
        key_com = f"item_{item_id}_comentario"

        raw_bool = form.get(key_bool)
        comentario = form.get(key_com, "")

        valor_bool = None
        if raw_bool is not None:
            valor_bool = raw_bool.lower() in ("true", "on", "1", "si", "sí")

        if item_id in existing_dict:
            r = existing_dict[item_id]
            r.valor_bool = valor_bool
            r.comentario = comentario.strip() or None
        else:
            r = InboundChecklistRespuesta(
                negocio_id=negocio_id,
                recepcion_id=recepcion_id,
                item_id=item_id,
                valor_bool=valor_bool,
                comentario=comentario.strip() or None,
                respondido_por_id=user["id"],
            )
            db.add(r)

    db.commit()

    log_inbound_event(
        "checklist_respuestas_guardadas",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        total_items=len(items_dict),
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )
