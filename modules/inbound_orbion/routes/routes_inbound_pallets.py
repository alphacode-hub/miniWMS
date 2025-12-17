# modules/inbound_orbion/routes/routes_inbound_pallets.py
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func

from core.database import get_db
from core.models.inbound.pallets import InboundPallet, InboundPalletItem
from core.models.inbound.lineas import InboundLinea 

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
from modules.inbound_orbion.services.inbound_linea_contract import normalizar_linea

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())

def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}success={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)

def _to_int_or_none(v: Any) -> int | None:
    s = ("" if v is None else str(v)).strip()
    if not s:
        return None
    if not s.isdigit():
        raise InboundDomainError("Valor entero inválido.")
    return int(s)

def _to_float_or_none(v: Any) -> float | None:
    # pesos/cantidades: 0 no sirve
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


def _to_float_allow_zero_or_none(v: Any) -> float | None:
    # temperatura: 0 sí es válido
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        n = float(s)
    except ValueError as exc:
        raise InboundDomainError("Valor numérico inválido.") from exc
    if n < 0:
        raise InboundDomainError("Este valor no puede ser negativo.")
    return n


def _assert_pallet_pertenece(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int) -> InboundPallet:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id or pallet.recepcion_id != recepcion_id:
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

    ok = request.query_params.get("success")
    error = request.query_params.get("error")

    pallets = (
        db.query(InboundPallet)
        .filter(InboundPallet.negocio_id == negocio_id, InboundPallet.recepcion_id == recepcion_id)
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
                func.coalesce(func.sum(InboundPalletItem.cantidad_estimada), 0).label("cant_est"),
                func.coalesce(func.sum(InboundPalletItem.peso_estimado_kg), 0).label("kg_est"),
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
                "cant_est": float(r.cant_est or 0),
                "kg_est": float(r.kg_est or 0),
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
            "qs_success": ok,
            "qs_error": error,
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

    ok = request.query_params.get("success")
    error = request.query_params.get("error")

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
        .filter(InboundLinea.negocio_id == negocio_id, InboundLinea.recepcion_id == recepcion_id)
        .order_by(InboundLinea.id.asc())
        .all()
    )
    


    # Construimos una vista UI (modo + pendientes)
    lineas_ui: list[dict[str, Any]] = []
    for l in lineas:
        try:
            v = normalizar_linea(l, allow_draft=False)
            modo = v.modo.value
            base = v.base_cantidad if modo == "CANTIDAD" else v.base_peso_kg
        except Exception:
            # si alguna línea está mala, la ocultamos del selector para evitar errores operativos
            continue

        # asignado en recepcion
        cant_asig = (
            db.query(func.coalesce(func.sum(InboundPalletItem.cantidad), 0))
            .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
            .filter(
                InboundPallet.negocio_id == negocio_id,
                InboundPallet.recepcion_id == recepcion_id,
                InboundPalletItem.linea_id == l.id,
            )
            .scalar()
            or 0
        )
        kg_asig = (
            db.query(func.coalesce(func.sum(InboundPalletItem.peso_kg), 0))
            .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
            .filter(
                InboundPallet.negocio_id == negocio_id,
                InboundPallet.recepcion_id == recepcion_id,
                InboundPalletItem.linea_id == l.id,
            )
            .scalar()
            or 0
        )

        if modo == "CANTIDAD":
            pend = float(base or 0) - float(cant_asig or 0)
            pend = max(pend, 0.0)
        else:
            pend = float(base or 0) - float(kg_asig or 0)
            pend = max(pend, 0.0)

        nombre = None
        if getattr(l, "producto", None) is not None and getattr(l.producto, "nombre", None):
            nombre = l.producto.nombre
        else:
            nombre = f"Línea #{l.id}"

        lineas_ui.append(
            {
                "id": l.id,
                "nombre": nombre,
                "modo": modo,
                "pendiente": round(pend, 3),
                "unidad": getattr(l, "unidad", None) or "unidad",
            }
        )

    return templates.TemplateResponse(
        "inbound_pallets_detalle.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallet": pallet,
            "pallet_items": pallet_items,
            "lineas": lineas,
            "lineas_ui": lineas_ui,
            "qs_success": ok,
            "qs_error": error,
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

    try:
        obtener_recepcion_editable(db, recepcion_id, negocio_id)

        pallet = crear_pallet_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            codigo_pallet=(codigo_pallet or "").strip(),
            peso_bruto_kg=_to_float_or_none(peso_bruto_kg),
            peso_tara_kg=_to_float_or_none(peso_tara_kg),
            bultos=_to_int_or_none(bultos),
            temperatura_promedio=_to_float_allow_zero_or_none(temperatura_promedio),
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

    except InboundDomainError as e:
        log_inbound_error(
            "pallet_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", error=e.message)


# ============================================================
# AGREGAR ITEM (service decide modo)
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

    try:
        obtener_recepcion_editable(db, recepcion_id, negocio_id)
        _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

        agregar_items_a_pallet(
            db=db,
            negocio_id=negocio_id,
            pallet_id=pallet_id,
            items=[{"linea_id": _to_int_or_none(linea_id), "cantidad": cantidad, "peso_kg": peso_kg}],
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Ítem agregado.")

    except InboundDomainError as e:
        log_inbound_error(
            "pallet_item_agregar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)


# ============================================================
# QUITAR ITEM
# ============================================================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/items/{pallet_item_id}/quitar", response_class=HTMLResponse)
async def inbound_pallet_item_quitar(
    recepcion_id: int,
    pallet_id: int,
    pallet_item_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        obtener_recepcion_editable(db, recepcion_id, negocio_id)
        _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)

        quitar_item_de_pallet(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            pallet_item_id=pallet_item_id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Ítem removido.")

    except InboundDomainError as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)


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
    try:
        obtener_recepcion_editable(db, recepcion_id, negocio_id)
        _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)
        marcar_pallet_listo(db, negocio_id, recepcion_id, pallet_id, user["id"])
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet cerrado.")
    except InboundDomainError as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/reabrir")
async def inbound_pallet_reabrir(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    try:
        obtener_recepcion_editable(db, recepcion_id, negocio_id)
        _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)
        reabrir_pallet(db, negocio_id, recepcion_id, pallet_id)
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Pallet reabierto.")
    except InboundDomainError as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/eliminar")
async def inbound_pallet_eliminar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    try:
        obtener_recepcion_editable(db, recepcion_id, negocio_id)
        _assert_pallet_pertenece(db, negocio_id, recepcion_id, pallet_id)
        eliminar_pallet_inbound(db, negocio_id, recepcion_id, pallet_id)
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet eliminado.")
    except InboundDomainError as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", error=e.message)
