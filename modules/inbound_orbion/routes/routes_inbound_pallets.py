from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func

from core.database import get_db
from core.models import InboundPallet, InboundPalletItem, InboundLinea

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,
)
from modules.inbound_orbion.services.services_inbound_pallets import (
    crear_pallet_inbound,
    eliminar_pallet_inbound,
    agregar_items_a_pallet,
    quitar_item_de_pallet,
    marcar_pallet_listo,
    reabrir_pallet,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================================================
# Helpers enterprise
# ============================================================

def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect(
    url: str,
    *,
    ok: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}success={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not s.isdigit():
        raise InboundDomainError("Valor entero inválido.")
    return int(s)


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        n = float(s)
    except ValueError as exc:
        raise InboundDomainError("Valor numérico inválido. Usa números (ej: 10 o 10.5).") from exc
    return n if n > 0 else None


def _assert_pallet_pertenece(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> InboundPallet:
    pallet = db.get(InboundPallet, pallet_id)
    if (
        not pallet
        or pallet.negocio_id != negocio_id
        or pallet.recepcion_id != recepcion_id
    ):
        raise InboundDomainError("Pallet inválido para esta recepción.")
    return pallet


# ============================================================
# LISTA DE PALLETS
# ============================================================

@router.get("/recepciones/{recepcion_id}/pallets", response_class=HTMLResponse)
async def inbound_pallets_lista(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    pallets = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .order_by(InboundPallet.id.asc())
        .all()
    )

    pallet_ids = [p.id for p in pallets]

    resumen: dict[int, dict[str, float]] = {}

    if pallet_ids:
        rows = (
            db.query(
                InboundPalletItem.pallet_id.label("pallet_id"),
                func.count(InboundPalletItem.id).label("n_items"),
                func.coalesce(func.sum(InboundPalletItem.cantidad), 0).label("cant_total"),
                func.coalesce(func.sum(InboundPalletItem.peso_kg), 0).label("kg_total"),
            )
            .filter(InboundPalletItem.pallet_id.in_(pallet_ids))
            .group_by(InboundPalletItem.pallet_id)
            .all()
        )
        for r in rows:
            resumen[int(r.pallet_id)] = {
                "n_items": int(r.n_items or 0),
                "cant": float(r.cant_total or 0),
                "kg": float(r.kg_total or 0),
            }

    log_inbound_event(
        "pallets_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        total=len(pallets),
    )

    return templates.TemplateResponse(
        "inbound_pallets.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallets": pallets,
            "resumen": resumen,
        },
    )


# ============================================================
# DETALLE PALLET
# ============================================================

@router.get("/recepciones/{recepcion_id}/pallets/{pallet_id}", response_class=HTMLResponse)
async def inbound_pallet_detalle(
    recepcion_id: int,
    pallet_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    pallet = _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

    pallet_items = (
        db.query(InboundPalletItem)
        .options(selectinload(InboundPalletItem.linea).selectinload(InboundLinea.producto))
        .filter(InboundPalletItem.pallet_id == pallet.id)
        .order_by(InboundPalletItem.id.asc())
        .all()
    )

    lineas = (
        db.query(InboundLinea)
        .options(selectinload(InboundLinea.producto))
        .filter(
            InboundLinea.negocio_id == negocio_id,
            InboundLinea.recepcion_id == recepcion_id,
        )
        .order_by(InboundLinea.id.asc())
        .all()
    )

    return templates.TemplateResponse(
        "inbound_pallets_detalle.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallet": pallet,
            "pallet_items": pallet_items,
            "lineas": lineas,  # el service decide si puede o no asignarse
        },
    )


# ============================================================
# NUEVO PALLET
# ============================================================

@router.post("/recepciones/{recepcion_id}/pallets/nuevo", response_class=HTMLResponse)
async def inbound_pallet_nuevo(
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    codigo_pallet: str = Form(...),
    peso_bruto_kg: str = Form(""),
    peso_tara_kg: str = Form(""),
    bultos: str = Form(""),
    temperatura_promedio: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]

    obtener_recepcion_editable(db, recepcion_id, negocio_id)

    pallet = crear_pallet_inbound(
        db=db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        codigo_pallet=codigo_pallet.strip(),
        peso_bruto_kg=_to_float_or_none(peso_bruto_kg),
        peso_tara_kg=_to_float_or_none(peso_tara_kg),
        bultos=_to_int_or_none(bultos),
        temperatura_promedio=_to_float_or_none(temperatura_promedio),
        observaciones=(observaciones or "").strip() or None,
        creado_por_id=user["id"],
    )

    log_inbound_event(
        "pallet_creado",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        pallet_id=pallet.id,
    )

    return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet creado.")


# ============================================================
# AGREGAR ITEM A PALLET (service decide modo)
# ============================================================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/items/agregar", response_class=HTMLResponse)
async def inbound_pallet_item_agregar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    linea_id: str = Form(...),
    cantidad: str = Form(""),
    peso_kg: str = Form(""),
):
    negocio_id = user["negocio_id"]

    obtener_recepcion_editable(db, recepcion_id, negocio_id)
    _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

    agregar_items_a_pallet(
        db=db,
        negocio_id=negocio_id,
        pallet_id=pallet_id,
        items=[
            {
                "linea_id": _to_int_or_none(linea_id),
                "cantidad": _to_float_or_none(cantidad),
                "peso_kg": _to_float_or_none(peso_kg),
            }
        ],
    )

    return _redirect(
        f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
        ok="Ítem agregado.",
    )


# ============================================================
# QUITAR ITEM
# ============================================================

@router.post(
    "/recepciones/{recepcion_id}/pallets/{pallet_id}/items/{pallet_item_id}/quitar",
    response_class=HTMLResponse,
)
async def inbound_pallet_item_quitar(
    recepcion_id: int,
    pallet_id: int,
    pallet_item_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    obtener_recepcion_editable(db, recepcion_id, negocio_id)
    _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

    quitar_item_de_pallet(
        db=db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
        pallet_item_id=pallet_item_id,
    )

    return _redirect(
        f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
        ok="Ítem removido.",
    )


# ============================================================
# CERRAR / REABRIR / ELIMINAR
# ============================================================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/cerrar")
async def inbound_pallet_cerrar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    obtener_recepcion_editable(db, recepcion_id, negocio_id)
    _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

    marcar_pallet_listo(db, negocio_id, recepcion_id, pallet_id, user["id"])
    return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet cerrado.")


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/reabrir")
async def inbound_pallet_reabrir(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    obtener_recepcion_editable(db, recepcion_id, negocio_id)
    _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

    reabrir_pallet(db, negocio_id, recepcion_id, pallet_id)
    return _redirect(
        f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
        ok="Pallet reabierto.",
    )


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/eliminar")
async def inbound_pallet_eliminar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    obtener_recepcion_editable(db, recepcion_id, negocio_id)
    _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

    eliminar_pallet_inbound(db, negocio_id, recepcion_id, pallet_id)
    return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet eliminado.")
