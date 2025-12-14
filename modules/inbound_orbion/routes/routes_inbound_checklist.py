# modules/inbound_orbion/routes/routes_inbound_checklist.py
"""
Rutas Checklist – Inbound ORBION

✔ Config checklist (items) por negocio
✔ Responder checklist por recepción
✔ Multi-tenant estricto (negocio_id)
✔ Logging estructurado inbound.*
✔ Reutiliza servicios de dominio (split)

Notas enterprise:
- Toda mutación pasa por servicios (evita lógica duplicada en rutas).
- Validamos/normalizamos inputs.
- Commit controlado, fallos logueados y traducidos a HTTP.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundChecklistItem, InboundChecklistRespuesta

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_checklist import (
    actualizar_checklist_item_inbound,
    crear_checklist_item_inbound,
    listar_checklist_items_inbound,
    registrar_respuestas_checklist_inbound,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_error,
    log_inbound_event,
)

from .inbound_common import get_negocio_or_404, inbound_roles_dep, templates

router = APIRouter()


# ============================
#   HELPERS
# ============================

def _to_int(value: Any, default: int = 1) -> int:
    try:
        v = int(value)
        return v if v > 0 else default
    except Exception:
        return default


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s in ("true", "on", "1", "si", "sí", "yes")


# ============================
#   CONFIG CHECKLIST (ITEMS)
# ============================

@router.get("/checklist/config", response_class=HTMLResponse)
async def inbound_checklist_config(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    items = listar_checklist_items_inbound(db=db, negocio_id=negocio_id, solo_activos=False)

    log_inbound_event(
        "checklist_config_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
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
    orden: int | None = Form(None),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    try:
        item = crear_checklist_item_inbound(
            db=db,
            negocio_id=negocio_id,
            texto=texto,
            orden=_to_int(orden, default=1) if orden is not None else None,
            activo=True,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "checklist_item_create_failed",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=400, detail=getattr(e, "message", str(e)))

    log_inbound_event(
        "checklist_item_creado",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        item_id=item.id,
    )

    return RedirectResponse(url="/inbound/checklist/config", status_code=302)


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
        raise HTTPException(status_code=404, detail="Ítem de checklist no encontrado")

    try:
        updated = actualizar_checklist_item_inbound(
            db=db,
            negocio_id=negocio_id,
            item_id=item_id,
            activo=not bool(item.activo),
        )
    except InboundDomainError as e:
        log_inbound_error(
            "checklist_item_toggle_failed",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            item_id=item_id,
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=400, detail=getattr(e, "message", str(e)))

    log_inbound_event(
        "checklist_item_toggle",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        item_id=updated.id,
        activo=bool(updated.activo),
    )

    return RedirectResponse(url="/inbound/checklist/config", status_code=302)


# ============================
#   RESPONDER CHECKLIST
# ============================

@router.get("/recepciones/{recepcion_id}/checklist", response_class=HTMLResponse)
async def inbound_checklist_responder_view(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        recepcion = obtener_recepcion_segura(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
    except InboundDomainError as e:
        log_inbound_error(
            "checklist_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    items = listar_checklist_items_inbound(db=db, negocio_id=negocio_id, solo_activos=True)

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
        user_email=user.get("email"),
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


@router.post("/recepciones/{recepcion_id}/checklist/guardar", response_class=HTMLResponse)
async def inbound_checklist_guardar(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    # Validar recepción (tenant)
    try:
        _ = obtener_recepcion_segura(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
    except InboundDomainError as e:
        log_inbound_error(
            "checklist_guardar_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    form = await request.form()

    # Lista de items activos para decidir qué llaves leer
    items = listar_checklist_items_inbound(db=db, negocio_id=negocio_id, solo_activos=True)
    items_ids = [i.id for i in items]

    payload_respuestas: list[dict[str, Any]] = []
    for item_id in items_ids:
        key_bool = f"item_{item_id}_valor"
        key_com = f"item_{item_id}_comentario"

        valor_bool = _to_bool(form.get(key_bool))
        comentario = (form.get(key_com) or "").strip() or None

        payload_respuestas.append(
            {
                "item_id": item_id,
                "valor_bool": valor_bool,
                "comentario": comentario,
            }
        )

    try:
        registrar_respuestas_checklist_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            respuestas=payload_respuestas,
            respondido_por_id=user.get("id"),
        )
    except InboundDomainError as e:
        log_inbound_error(
            "checklist_guardar_failed",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=400, detail=getattr(e, "message", str(e)))

    log_inbound_event(
        "checklist_respuestas_guardadas",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        total_items=len(items_ids),
    )

    return RedirectResponse(url=f"/inbound/recepciones/{recepcion_id}", status_code=302)
