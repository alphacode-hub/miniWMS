# modules/inbound_orbion/routes/routes_inbound_pallets.py

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func
from urllib.parse import quote_plus

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
# Helpers locales
# ============================================================

def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect_error(recepcion_id: int, pallet_id: int, msg: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}?error={_qp(msg)}",
        status_code=302,
    )


def _redirect_lista(recepcion_id: int) -> RedirectResponse:
    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/pallets",
        status_code=302,
    )


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


# ============================
#   LISTA DE PALLETS
# ============================

@router.get("/recepciones/{recepcion_id}/pallets", response_class=HTMLResponse)
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

    # Pallets
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

    # Si no hay pallets, igual renderizamos sin romper
    lineas_por_pallet: dict[int, int] = {}
    cant_por_pallet: dict[int, float] = {}
    kg_por_pallet: dict[int, float] = {}
    kpi_con_lineas = 0
    kpi_en_proceso = 0
    kpi_listos = 0

    if pallet_ids:
        # Agrupados por pallet: cantidad de items (líneas), sum(cantidad), sum(kg)
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
            pid = int(r.pallet_id)
            lineas_por_pallet[pid] = int(r.n_items or 0)
            cant_por_pallet[pid] = float(r.cant_total or 0)
            kg_por_pallet[pid] = float(r.kg_total or 0)

        kpi_con_lineas = sum(
            1 for pid in pallet_ids if lineas_por_pallet.get(pid, 0) > 0
        )

        # KPIs por estado real del pallet (enterprise)
        for p in pallets:
            estado = (p.estado or "ABIERTO").upper()
            if estado == "LISTO":
                kpi_listos += 1
            elif estado in ("EN_PROCESO", "ABIERTO"):
                kpi_en_proceso += 1

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
            "lineas_por_pallet": lineas_por_pallet,
            "cant_por_pallet": cant_por_pallet,
            "kg_por_pallet": kg_por_pallet,
            "kpi_total_pallets": len(pallets),
            "kpi_con_lineas": kpi_con_lineas,
            "kpi_en_proceso": kpi_en_proceso,
            "kpi_listos": kpi_listos,
        },
    )


# ============================
#   DETALLE DE PALLET
# ============================

@router.get("/recepciones/{recepcion_id}/pallets/{pallet_id}", response_class=HTMLResponse)
async def inbound_pallets_detalle(
    recepcion_id: int,
    pallet_id: int,
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
            "pallet_detalle_recepcion_not_found",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    # Buscar pallet (seguro por negocio y recepción)
    pallet = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPallet.id == pallet_id,
        )
        .first()
    )

    if not pallet:
        log_inbound_error(
            "pallet_detalle_not_found",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )
        raise HTTPException(status_code=404, detail="Pallet no encontrado")

    log_inbound_event(
        "pallet_detalle_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
    )

    # pallet_items (del pallet actual) con linea+producto para render
    pallet_items = (
        db.query(InboundPalletItem)
        .options(
            selectinload(InboundPalletItem.linea).selectinload(InboundLinea.producto)
        )
        .filter(InboundPalletItem.pallet_id == pallet.id)
        .order_by(InboundPalletItem.id.asc())
        .all()
    )

    # Todas las líneas de la recepción (con producto)
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

    # Sumatoria asignada a cualquier pallet de esta recepción (por linea_id)
    asignaciones = (
        db.query(
            InboundPalletItem.linea_id.label("linea_id"),
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0).label("cant_asignada"),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0).label("kg_asignado"),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .group_by(InboundPalletItem.linea_id)
        .all()
    )

    asign_map = {int(row.linea_id): row for row in asignaciones}

    # ViewModel para template
    lineas_vm: list[dict] = []
    for ln in lineas:
        row = asign_map.get(int(ln.id))
        cant_asig = float(row.cant_asignada) if row else 0.0
        kg_asig = float(row.kg_asignado) if row else 0.0

        cant_base = (
            ln.cantidad_recibida
            if ln.cantidad_recibida is not None
            else ln.cantidad_esperada
        )
        kg_base = getattr(ln, "kilos", None)
        if kg_base is None:
            kg_base = getattr(ln, "peso_kg", None)

        cant_pend = (float(cant_base) - cant_asig) if cant_base is not None else None
        kg_pend = (float(kg_base) - kg_asig) if kg_base is not None else None

        permite_cantidad = (cant_pend is not None and cant_pend > 0)
        permite_peso = (kg_pend is not None and kg_pend > 0)

        lineas_vm.append(
            {
                "linea": ln,
                "cant_base": cant_base,
                "kg_base": kg_base,
                "cant_asig": cant_asig,
                "kg_asig": kg_asig,
                "cant_pend": cant_pend,
                "kg_pend": kg_pend,
                "permite_cantidad": permite_cantidad,
                "permite_peso": permite_peso,
                "disponible": (permite_cantidad or permite_peso),
            }
        )

    # Para el select: solo líneas con pendiente real
    lineas_disponibles = [x for x in lineas_vm if x["disponible"]]

    return templates.TemplateResponse(
        "inbound_pallets_detalle.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallet": pallet,
            "modulo_nombre": "Orbion Inbound",
            "pallet_items": pallet_items,
            "lineas_disponibles": lineas_disponibles,
        },
    )


# ============================
#   NUEVO PALLET
# ============================

@router.post("/recepciones/{recepcion_id}/pallets/nuevo", response_class=HTMLResponse)
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

    # Enterprise: editable
    try:
        recepcion = obtener_recepcion_editable(
            db=db,
            recepcion_id=recepcion_id,
            negocio_id=negocio_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "pallet_crear_recepcion_not_editable",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    try:
        pallet = crear_pallet_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion.id,
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

    return _redirect_lista(recepcion_id)


# ============================
#   AGREGAR ITEM A PALLET
# ============================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/items/agregar", response_class=HTMLResponse)
async def inbound_pallet_item_agregar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    linea_id: str = Form(...),         # tolera HTML imperfecto
    cantidad: str | None = Form(None), # tolera ""
    peso_kg: str | None = Form(None),  # tolera ""
):
    negocio_id = user["negocio_id"]

    def _to_int(v: str) -> int:
        v = (v or "").strip()
        if not v.isdigit():
            raise InboundDomainError("Debes seleccionar una línea válida.")
        return int(v)

    def _to_float(v: str | None) -> float | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        v = v.replace(",", ".")
        try:
            n = float(v)
        except ValueError:
            raise InboundDomainError("Cantidad/Peso inválido. Usa números (ej: 10 o 10.5).")
        return n if n > 0 else None

    try:
        # Enterprise: editable + multi-tenant
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

        # pallet debe pertenecer a esta recepción
        _assert_pallet_pertenece(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        linea_id_int = _to_int(linea_id)
        cantidad_f = _to_float(cantidad)
        peso_kg_f = _to_float(peso_kg)

        if cantidad_f is None and peso_kg_f is None:
            raise InboundDomainError("Debes ingresar una cantidad o un peso mayor a cero.")

        agregar_items_a_pallet(
            db=db,
            negocio_id=negocio_id,
            pallet_id=pallet_id,
            items=[{"linea_id": linea_id_int, "cantidad": cantidad_f, "peso_kg": peso_kg_f}],
        )

    except InboundDomainError as e:
        return _redirect_error(recepcion_id, pallet_id, e.message)

    except Exception as e:
        log_inbound_error(
            "pallet_item_agregar_unhandled",
            negocio_id=negocio_id,
            user_email=user["email"],
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=str(e),
        )
        return _redirect_error(recepcion_id, pallet_id, "Error inesperado al agregar ítem. Revisa logs.")

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
        status_code=302,
    )


# ============================
#   QUITAR ITEM DE PALLET
# ============================

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
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        _assert_pallet_pertenece(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        quitar_item_de_pallet(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            pallet_item_id=pallet_item_id,
        )
    except InboundDomainError as e:
        return _redirect_error(recepcion_id, pallet_id, e.message)

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
        status_code=302,
    )


# ============================
#   ELIMINAR PALLET
# ============================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/eliminar", response_class=HTMLResponse)
async def inbound_pallets_eliminar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        _assert_pallet_pertenece(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

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

    return _redirect_lista(recepcion_id)


# ============================
#   MARCAR PALLET COMO LISTO
# ============================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/cerrar", response_class=HTMLResponse)
async def inbound_pallet_cerrar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        _assert_pallet_pertenece(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        marcar_pallet_listo(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            user_id=user["id"],
        )
    except InboundDomainError as e:
        return _redirect_error(recepcion_id, pallet_id, e.message)

    log_inbound_event(
        "pallet_cerrado",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
    )

    return _redirect_lista(recepcion_id)


# ============================
#   REABRIR PALLET
# ============================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/reabrir", response_class=HTMLResponse)
async def inbound_pallet_reabrir(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        _assert_pallet_pertenece(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        reabrir_pallet(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )
    except InboundDomainError as e:
        return _redirect_error(recepcion_id, pallet_id, e.message)

    log_inbound_event(
        "pallet_reabierto",
        negocio_id=negocio_id,
        user_email=user["email"],
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
    )

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
        status_code=302,
    )
