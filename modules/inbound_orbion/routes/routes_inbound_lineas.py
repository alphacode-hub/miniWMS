# modules/inbound_orbion/routes/routes_inbound_lineas.py
from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import Producto
from core.models.inbound.recepciones import InboundRecepcion

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_incidencias import (
    obtener_resumen_incidencias_cuantitativo,
)
from modules.inbound_orbion.services.services_inbound_lineas import (
    actualizar_linea_inbound,
    crear_linea_inbound,
    eliminar_linea_inbound,
    listar_lineas_recepcion,
    obtener_linea,
)
from modules.inbound_orbion.services.services_inbound_productos_bridge import (
    crear_producto_rapido_inbound,
)
from modules.inbound_orbion.services.services_inbound_reconciliacion import (
    reconciliar_recepcion,
)

from .inbound_common import inbound_roles_dep, templates

router = APIRouter()


# ============================================================
# Helpers (baseline aligned)
# ============================================================

def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    # Compat: aceptamos ok/success de ida; pero escribimos ok/error (más simple).
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _negocio_id_from_user(user) -> int:
    """
    Compatible con user dict (session) o modelo Usuario.
    """
    if isinstance(user, dict):
        nid = user.get("negocio_id")
        if not nid:
            raise InboundDomainError("No se encontró negocio_id en la sesión.")
        return int(nid)

    nid = getattr(user, "negocio_id", None)
    if not nid:
        raise InboundDomainError("No se encontró negocio_id en la sesión.")
    return int(nid)


def _parse_date_iso(v: str | None) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _to_str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        s = s.replace(",", ".")
    else:
        s = v
    try:
        return float(s)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor numérico inválido. Usa números (ej: 10 o 10.5).") from exc


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        # Permitimos negativos? para líneas no.
        if not s.isdigit():
            raise InboundDomainError("Valor entero inválido.")
        return int(s)
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor entero inválido.") from exc


def _load_recepcion_or_404(db: Session, negocio_id: int, recepcion_id: int) -> InboundRecepcion:
    r = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.id == recepcion_id,
            InboundRecepcion.negocio_id == negocio_id,
        )
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="Recepción no encontrada")
    return r


def _listar_productos_activos(db: Session, negocio_id: int) -> list[Producto]:
    return (
        db.query(Producto)
        .filter(Producto.negocio_id == negocio_id, Producto.activo == 1)
        .order_by(Producto.nombre.asc())
        .all()
    )


# ============================================================
# LISTA
# ============================================================

@router.get("/recepciones/{recepcion_id}/lineas", response_class=HTMLResponse)
async def inbound_lineas_lista(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    # compat qs
    qs_success = request.query_params.get("success") or request.query_params.get("ok")
    qs_error = request.query_params.get("error")

    # show recon
    show_recon = (request.query_params.get("recon") == "1")

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        # bool UI (si está cerrada, obtener_recepcion_editable puede lanzar)
        try:
            _ = obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
            recepcion_editable = True
        except Exception:
            recepcion_editable = False

        lineas = listar_lineas_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        # ✅ incidencias: resumen cuantitativo para "explicado por incidencias"
        inc_resumen = obtener_resumen_incidencias_cuantitativo(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            include_cerradas=True,
            exclude_canceladas=True,
        )

        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "lineas": lineas,
                "recepcion_editable": recepcion_editable,
                "qs_success": qs_success,
                "qs_error": qs_error,
                "show_recon": show_recon,
                "inc_resumen": inc_resumen,
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "lineas": [],
                "recepcion_editable": True,
                "qs_success": None,
                "qs_error": str(getattr(e, "message", None) or e),
                "show_recon": show_recon,
                "inc_resumen": {"totales": {"count": 0, "qty": 0.0, "kg": 0.0}, "por_linea": {}},
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=404,
        )

    except Exception:
        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "lineas": [],
                "recepcion_editable": True,
                "qs_success": None,
                "qs_error": "Error inesperado al abrir líneas. Revisa logs.",
                "show_recon": show_recon,
                "inc_resumen": {"totales": {"count": 0, "qty": 0.0, "kg": 0.0}, "por_linea": {}},
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )


# ============================================================
# FORM NUEVA
# ============================================================

@router.get("/recepciones/{recepcion_id}/lineas/nueva", response_class=HTMLResponse)
async def inbound_nueva_linea_form(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        # Solo permitir abrir form si editable (si no, redirigir a lista)
        _ = obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
    except Exception as e:
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", error=str(getattr(e, "message", None) or e))

    error = request.query_params.get("error")
    recepcion = _load_recepcion_or_404(db, negocio_id, recepcion_id)
    productos = _listar_productos_activos(db, negocio_id)

    return templates.TemplateResponse(
        "inbound_linea_form.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "productos": productos,
            "error": error,
            "form_action": f"/inbound/recepciones/{recepcion_id}/lineas",
        },
    )


# ============================================================
# CREAR LINEA
# ============================================================

@router.post("/recepciones/{recepcion_id}/lineas", response_class=HTMLResponse)
async def inbound_agregar_linea(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    # Producto
    producto_id: str = Form(""),
    nuevo_producto_nombre: str = Form(""),
    nuevo_producto_unidad_base: str = Form(""),
    # Línea
    lote: str = Form(""),
    fecha_vencimiento: str = Form(""),
    unidad: str = Form(""),
    observaciones: str = Form(""),
    bultos: str = Form(""),
    cantidad_documento: str = Form(""),
    kilos: str = Form(""),
    # cantidad_recibida se permite en crear (pero en tu baseline: solo reconcilia; lo dejamos disponible)
    cantidad_recibida: str = Form(""),
    temperatura_objetivo: str = Form(""),
    temperatura_recibida: str = Form(""),
    # Overrides
    peso_unitario_kg_override: str = Form(""),
    unidades_por_bulto_override: str = Form(""),
    peso_por_bulto_kg_override: str = Form(""),
    nombre_bulto_override: str = Form(""),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

        # Resolver producto
        producto_obj: Producto | None = None
        pid = _to_int_or_none(producto_id)

        if pid:
            producto_obj = (
                db.query(Producto)
                .filter(
                    Producto.id == pid,
                    Producto.negocio_id == negocio_id,
                    Producto.activo == 1,
                )
                .first()
            )
            if not producto_obj:
                raise InboundDomainError("El producto seleccionado no es válido para este negocio.")

        nuevo_nombre = _to_str_or_none(nuevo_producto_nombre)
        nuevo_unidad = _to_str_or_none(nuevo_producto_unidad_base)

        if not producto_obj and nuevo_nombre:
            producto_obj = crear_producto_rapido_inbound(
                db,
                negocio_id=negocio_id,
                nombre=nuevo_nombre,
                unidad=nuevo_unidad,
            )

        if not producto_obj:
            raise InboundDomainError("Debes seleccionar un producto o ingresar un producto rápido.")

        fecha_ven_dt = _parse_date_iso(fecha_vencimiento)

        cant_doc = _to_float_or_none(cantidad_documento)
        kg_doc = _to_float_or_none(kilos)

        cant_rec = _to_float_or_none(cantidad_recibida)
        temp_obj = _to_float_or_none(temperatura_objetivo)
        temp_rec = _to_float_or_none(temperatura_recibida)
        bultos_i = _to_int_or_none(bultos)

        pu_ov = _to_float_or_none(peso_unitario_kg_override)
        ub_ov = _to_int_or_none(unidades_por_bulto_override)
        pb_ov = _to_float_or_none(peso_por_bulto_kg_override)
        nb_ov = _to_str_or_none(nombre_bulto_override)

        _ = crear_linea_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            producto_id=producto_obj.id,
            lote=_to_str_or_none(lote),
            fecha_vencimiento=fecha_ven_dt,
            cantidad_esperada=cant_doc,
            cantidad_recibida=cant_rec,
            unidad=_to_str_or_none(unidad),
            temperatura_objetivo=temp_obj,
            temperatura_recibida=temp_rec,
            observaciones=_to_str_or_none(observaciones),
            peso_kg=kg_doc,
            bultos=bultos_i,
            peso_unitario_kg_override=pu_ov,
            unidades_por_bulto_override=ub_ov,
            peso_por_bulto_kg_override=pb_ov,
            nombre_bulto_override=nb_ov,
        )

        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", ok="Línea creada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas/nueva",
            error=str(getattr(e, "message", None) or e),
        )

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas/nueva",
            error="Error inesperado al crear línea. Revisa logs.",
        )


# ============================================================
# EDITAR (GET)
# ============================================================

@router.get("/recepciones/{recepcion_id}/lineas/{linea_id}/editar", response_class=HTMLResponse)
async def inbound_editar_linea_form(
    request: Request,
    recepcion_id: int,
    linea_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    recepcion = _load_recepcion_or_404(db, negocio_id, recepcion_id)

    # multi-tenant + existencia
    linea = obtener_linea(db, negocio_id=negocio_id, linea_id=linea_id)
    if int(linea.recepcion_id) != int(recepcion_id):
        raise HTTPException(status_code=404, detail="Línea no pertenece a esta recepción")

    # editable bool para UI
    try:
        _ = obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        recepcion_editable = True
    except Exception:
        recepcion_editable = False

    es_draft = bool(getattr(linea, "es_draft", 0) == 1)

    error = request.query_params.get("error")
    ok = request.query_params.get("success") or request.query_params.get("ok")

    return templates.TemplateResponse(
        "inbound_linea_editar.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "linea": linea,
            "error": error,
            "qs_success": ok,
            "recepcion_editable": recepcion_editable,
            "es_draft": es_draft,
            "form_action": f"/inbound/recepciones/{recepcion_id}/lineas/{linea_id}/editar",
        },
    )


# ============================================================
# EDITAR (POST) - guarda borrador o finaliza
# ============================================================

@router.post("/recepciones/{recepcion_id}/lineas/{linea_id}/editar", response_class=HTMLResponse)
async def inbound_editar_linea_submit(
    request: Request,
    recepcion_id: int,
    linea_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    # acción
    save_mode: str = Form("final"),  # "draft" o "final"
    # Línea
    lote: str = Form(""),
    fecha_vencimiento: str = Form(""),
    unidad: str = Form(""),
    observaciones: str = Form(""),
    bultos: str = Form(""),
    cantidad_documento: str = Form(""),
    kilos: str = Form(""),
    # cantidad_recibida: baseline enterprise => se gestiona por reconciliación (ignoramos)
    cantidad_recibida: str = Form(""),
    temperatura_objetivo: str = Form(""),
    temperatura_recibida: str = Form(""),
    # overrides
    peso_unitario_kg_override: str = Form(""),
    unidades_por_bulto_override: str = Form(""),
    peso_por_bulto_kg_override: str = Form(""),
    nombre_bulto_override: str = Form(""),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

        linea = obtener_linea(db, negocio_id=negocio_id, linea_id=linea_id)
        if int(linea.recepcion_id) != int(recepcion_id):
            raise HTTPException(status_code=404, detail="Línea no pertenece a esta recepción")

        es_draft = bool(getattr(linea, "es_draft", 0) == 1)

        want_draft = (save_mode or "").strip().lower() == "draft"
        fecha_ven_dt = _parse_date_iso(fecha_vencimiento)

        updates: dict[str, Any] = {
            "lote": _to_str_or_none(lote),
            "fecha_vencimiento": fecha_ven_dt,
            "unidad": _to_str_or_none(unidad),
            "observaciones": _to_str_or_none(observaciones),
            "bultos": _to_int_or_none(bultos),
            "cantidad_documento": _to_float_or_none(cantidad_documento),
            "peso_kg": _to_float_or_none(kilos),
            # "cantidad_recibida": IGNORADO
            "temperatura_objetivo": _to_float_or_none(temperatura_objetivo),
            "temperatura_recibida": _to_float_or_none(temperatura_recibida),
            "peso_unitario_kg_override": _to_float_or_none(peso_unitario_kg_override),
            "unidades_por_bulto_override": _to_int_or_none(unidades_por_bulto_override),
            "peso_por_bulto_kg_override": _to_float_or_none(peso_por_bulto_kg_override),
            "nombre_bulto_override": _to_str_or_none(nombre_bulto_override),
        }

        # ----------------------------
        # DRAFT SAVE (suave)
        # ----------------------------
        if want_draft:
            if not es_draft:
                raise InboundDomainError("Esta línea ya es oficial. No se puede guardar como borrador.")

            for k, v in updates.items():
                if hasattr(linea, k):
                    setattr(linea, k, v)

            db.commit()
            return _redirect(
                f"/inbound/recepciones/{recepcion_id}/lineas/{linea_id}/editar",
                ok="Borrador guardado.",
            )

        # ----------------------------
        # FINAL SAVE (estricto)
        # ----------------------------
        if es_draft and not getattr(linea, "producto_id", None):
            raise InboundDomainError(
                "Este borrador no tiene producto asignado. Elimina la línea y crea una nueva con producto válido."
            )

        if es_draft and hasattr(linea, "es_draft"):
            setattr(linea, "es_draft", 0)

        _ = actualizar_linea_inbound(
            db=db,
            negocio_id=negocio_id,
            linea_id=linea_id,
            **updates,
        )

        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", ok="Línea actualizada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas/{linea_id}/editar",
            error=str(getattr(e, "message", None) or e),
        )

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas/{linea_id}/editar",
            error="Error inesperado al editar. Revisa logs.",
        )


# ============================================================
# ELIMINAR
# ============================================================

@router.post("/recepciones/{recepcion_id}/lineas/{linea_id}/eliminar", response_class=HTMLResponse)
async def inbound_eliminar_linea(
    request: Request,
    recepcion_id: int,
    linea_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

        eliminar_linea_inbound(db=db, negocio_id=negocio_id, linea_id=linea_id)

        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", ok="Línea eliminada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas",
            error=str(getattr(e, "message", None) or e),
        )

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas",
            error="Error inesperado al eliminar línea. Revisa logs.",
        )


# ============================================================
# RECONCILIAR (BOTÓN)
# ============================================================

@router.post("/recepciones/{recepcion_id}/lineas/reconciliar", response_class=HTMLResponse)
async def inbound_lineas_reconciliar(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        _ = obtener_recepcion_segura(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        resumen = reconciliar_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        tot = (resumen.get("totales") or {})
        kg = tot.get("fisico_kg", 0.0)
        qty = tot.get("fisico_cantidad", 0.0)

        msg = (
            f"Reconciliación OK · líneas {resumen.get('lineas_actualizadas', 0)}/{resumen.get('lineas_total', 0)}"
            f" · kg {kg} · cant {qty}"
        )

        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas?recon=1", ok=msg)

    except InboundDomainError as e:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas",
            error=str(getattr(e, "message", None) or e),
        )

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas",
            error="Error inesperado al reconciliar. Revisa logs.",
        )
