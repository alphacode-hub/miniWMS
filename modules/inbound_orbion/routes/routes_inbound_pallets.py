# modules/inbound_orbion/routes/routes_inbound_pallets.py
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from core.database import get_db
from core.models.inbound.lineas import InboundLinea
from core.models.inbound.pallets import InboundPallet, InboundPalletItem

from modules.inbound_orbion.services.inbound_linea_contract import normalizar_linea
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_error,
    log_inbound_event,
)
from modules.inbound_orbion.services.services_inbound_pallets import (
    agregar_items_a_pallet,
    construir_resumen_pallets,
    crear_pallet_inbound,
    editar_pallet_inbound,
    eliminar_pallet_inbound,
    marcar_pallet_listo,
    obtener_pallet_seguro,
    quitar_item_de_pallet,
    reabrir_pallet,
)

# ✅ FIX: reconciliar después de mutaciones de pallets/items
from modules.inbound_orbion.services.services_inbound_reconciliacion import (
    reconciliar_recepcion,
)

from .inbound_common import inbound_roles_dep, templates

router = APIRouter()


# ============================================================
# Utils
# ============================================================

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
    """
    Pesos/cantidades: 0 o vacío => None.
    Acepta coma decimal.
    """
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
    """
    Temperatura: 0 sí es válido.
    Enterprise actual: no permite negativos (si quieres permitir negativos después, ajustamos aquí).
    """
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


def _pallet_estado_up(pallet: InboundPallet) -> str:
    estado_txt = (pallet.estado.value if pallet.estado is not None else str(pallet.estado))
    return str(estado_txt).replace("PalletEstado.", "").upper()


def _post_mutation_recon(db: Session, *, negocio_id: int, recepcion_id: int) -> None:
    """
    Enterprise FIX:
    Cada vez que mutas pallets/items, recalculamos el snapshot de conciliación
    en InboundLinea para que la lista de líneas NO quede con valores pegados.

    strict=True (baseline): solo pallets LISTO cuentan.
    - Si un pallet LISTO se elimina o se reabre => el físico vuelve a 0.
    - Si se marca LISTO => físico se actualiza.
    """
    # Asegura que la DB "vea" los cambios del transaction actual.
    db.flush()

    reconciliar_recepcion(
        db=db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        include_lineas=False,
        require_editable=False,  # no bloquea si luego esto se usa en lectura
        strict=True,
        commit=False,  # commit se hace al final de la ruta
    )


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
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .order_by(InboundPallet.id.asc())
        .all()
    )

    resumen = construir_resumen_pallets(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

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

    recepcion_editable = True
    try:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    except InboundDomainError:
        recepcion_editable = False

    pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    ok = request.query_params.get("success")
    error = request.query_params.get("error")

    pallet_items = (
        db.query(InboundPalletItem)
        .options(selectinload(InboundPalletItem.linea).selectinload(InboundLinea.producto))
        .filter(
            InboundPalletItem.pallet_id == pallet.id,
            InboundPalletItem.negocio_id == negocio_id,
        )
        .order_by(InboundPalletItem.id.asc())
        .all()
    )

    lineas = (
        db.query(InboundLinea)
        .options(selectinload(InboundLinea.producto))
        .filter(
            InboundLinea.negocio_id == negocio_id,
            InboundLinea.recepcion_id == recepcion_id,
            InboundLinea.activo == 1,
        )
        .order_by(InboundLinea.id.asc())
        .all()
    )

    sums = (
        db.query(
            InboundPalletItem.linea_id.label("linea_id"),
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0.0).label("cant_asig"),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0.0).label("kg_asig"),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .group_by(InboundPalletItem.linea_id)
        .all()
    )
    sums_by_linea = {int(r.linea_id): (float(r.cant_asig or 0.0), float(r.kg_asig or 0.0)) for r in sums}

    lineas_ui: list[dict[str, Any]] = []
    for l in lineas:
        try:
            v = normalizar_linea(l, allow_draft=False)
            modo = v.modo.value
            base = v.base_cantidad if modo == "CANTIDAD" else v.base_peso_kg
        except Exception:
            continue

        cant_asig, kg_asig = sums_by_linea.get(int(l.id), (0.0, 0.0))

        if modo == "CANTIDAD":
            pend = float(base or 0.0) - float(cant_asig or 0.0)
        else:
            pend = float(base or 0.0) - float(kg_asig or 0.0)

        pend = max(pend, 0.0)

        prod = getattr(l, "producto", None)
        nombre = getattr(prod, "nombre", None) if prod is not None else None
        if not nombre:
            nombre = f"Línea #{l.id}"

        peso_unitario_kg = None
        try:
            v1 = getattr(l, "peso_unitario_kg_override", None)
            if v1 is not None and float(v1) > 0:
                peso_unitario_kg = float(v1)
            elif prod is not None:
                v2 = getattr(prod, "peso_unitario_kg", None)
                if v2 is not None and float(v2) > 0:
                    peso_unitario_kg = float(v2)
        except Exception:
            peso_unitario_kg = None

        unidad = getattr(l, "unidad", None) or (getattr(prod, "unidad", None) if prod else None) or "unidad"

        lineas_ui.append(
            {
                "id": l.id,
                "nombre": nombre,
                "modo": modo,
                "pendiente": round(pend, 3),
                "unidad": unidad,
                "peso_unitario_kg": peso_unitario_kg,
            }
        )

    log_inbound_event(
        "pallet_detalle_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
        items=len(pallet_items),
        lineas=len(lineas_ui),
    )

    return templates.TemplateResponse(
        "inbound_pallets_detalle.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallet": pallet,
            "pallet_items": pallet_items,
            "lineas_ui": lineas_ui,
            "qs_success": ok,
            "qs_error": error,
            "recepcion_editable": recepcion_editable,
        },
    )


# ============================================================
# EDITAR PALLET (GET/POST)
# ============================================================

@router.get("/recepciones/{recepcion_id}/pallets/{pallet_id}/editar", response_class=HTMLResponse)
async def inbound_pallet_editar_get(
    recepcion_id: int,
    pallet_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    try:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    except InboundDomainError as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)

    pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    estado_up = _pallet_estado_up(pallet)
    if estado_up in {"LISTO", "BLOQUEADO"}:
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}",
            error="Pallet en estado solo lectura.",
        )

    ok = request.query_params.get("success")
    error = request.query_params.get("error")

    log_inbound_event(
        "pallet_editar_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
        pallet_id=pallet_id,
    )

    return templates.TemplateResponse(
        "inbound_pallet_editar.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "pallet": pallet,
            "qs_success": ok,
            "qs_error": error,
            "recepcion_editable": True,
        },
    )


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/editar", response_class=HTMLResponse)
async def inbound_pallet_editar_post(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    peso_bruto_kg: str = Form(""),
    peso_tara_kg: str = Form(""),
    bultos: str = Form(""),
    temperatura_promedio: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]

    try:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
        pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        estado_up = _pallet_estado_up(pallet)
        if estado_up in {"LISTO", "BLOQUEADO"}:
            raise InboundDomainError("Pallet en estado solo lectura.")

        editar_pallet_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            bultos=_to_int_or_none(bultos),
            temperatura_promedio=_to_float_allow_zero_or_none(temperatura_promedio),
            observaciones=(observaciones or "").strip() or None,
            peso_bruto_kg=_to_float_or_none(peso_bruto_kg),
            peso_tara_kg=_to_float_or_none(peso_tara_kg),
        )

        # (editar metadata no cambia conciliación de líneas)

        db.commit()

        log_inbound_event(
            "pallet_editado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Pallet actualizado.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_editar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}/editar", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}/editar", error="Error inesperado.")


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
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)

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
            creado_por_id=user.get("id"),
        )

        db.commit()

        log_inbound_event(
            "pallet_creado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet.id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet creado.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", error="Error inesperado al crear pallet.")


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
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
        pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        linea_id_i = _to_int_or_none(linea_id)
        if not linea_id_i:
            raise InboundDomainError("Debes seleccionar una línea válida.")

        cant_f = _to_float_or_none(cantidad)
        kg_f = _to_float_or_none(peso_kg)

        agregar_items_a_pallet(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            items=[{"linea_id": linea_id_i, "cantidad": cant_f, "peso_kg": kg_f}],
        )

        # ✅ Reconciliación post-mutation (solo afecta snapshot si pallet LISTO)
        _post_mutation_recon(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        db.commit()

        log_inbound_event(
            "pallet_item_agregado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            linea_id=linea_id_i,
            pallet_estado=_pallet_estado_up(pallet),
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Ítem agregado.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_item_agregar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error="Error inesperado al agregar ítem.")


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
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
        pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        quitar_item_de_pallet(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            pallet_item_id=pallet_item_id,
        )

        # ✅ Reconciliación post-mutation
        _post_mutation_recon(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        db.commit()

        log_inbound_event(
            "pallet_item_quitado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            pallet_item_id=pallet_item_id,
            pallet_estado=_pallet_estado_up(pallet),
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Ítem removido.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_item_quitar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error="Error inesperado al quitar ítem.")


# ============================================================
# CERRAR / REABRIR / ELIMINAR
# ============================================================

@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/cerrar", response_class=HTMLResponse)
async def inbound_pallet_cerrar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    try:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
        pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        marcar_pallet_listo(db, negocio_id, recepcion_id, pallet_id, user["id"])

        # ✅ Al pasar a LISTO, ahora sí cuenta para strict=True → actualiza snapshot
        _post_mutation_recon(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        db.commit()

        log_inbound_event(
            "pallet_cerrado_listo",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Pallet marcado LISTO.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_cerrar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error="Error inesperado al cerrar pallet.")


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/reabrir", response_class=HTMLResponse)
async def inbound_pallet_reabrir(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    try:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
        pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        reabrir_pallet(db, negocio_id, recepcion_id, pallet_id)

        # ✅ Si estaba LISTO, deja de contar → snapshot vuelve a 0 (strict=True)
        _post_mutation_recon(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        db.commit()

        log_inbound_event(
            "pallet_reabierto",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", ok="Pallet reabierto.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_reabrir_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets/{pallet_id}", error="Error inesperado al reabrir pallet.")


@router.post("/recepciones/{recepcion_id}/pallets/{pallet_id}/eliminar", response_class=HTMLResponse)
async def inbound_pallet_eliminar(
    recepcion_id: int,
    pallet_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    try:
        _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
        _ = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

        eliminar_pallet_inbound(db, negocio_id, recepcion_id, pallet_id)

        # ✅ Si el pallet eliminado era LISTO, ahora ya no cuenta → snapshot se recalcula
        _post_mutation_recon(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        db.commit()

        log_inbound_event(
            "pallet_eliminado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", ok="Pallet eliminado.")

    except InboundDomainError as e:
        db.rollback()
        log_inbound_error(
            "pallet_eliminar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            pallet_id=pallet_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", error=e.message)

    except Exception:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/pallets", error="Error inesperado al eliminar pallet.")
