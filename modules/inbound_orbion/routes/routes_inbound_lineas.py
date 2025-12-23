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
from core.services.services_audit import audit, AuditAction

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_lineas import (
    crear_linea_inbound,
    eliminar_linea_inbound,
    listar_lineas_recepcion,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_error,
    log_inbound_event,
)
from modules.inbound_orbion.services.services_inbound_reconciliacion import (
    reconciliar_recepcion,
)

from .inbound_common import inbound_roles_dep, templates

router = APIRouter()


# ============================================================
# Helpers ultra enterprise
# ============================================================

def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    """
    Redirección consistente para UI (mensajes por querystring).
    """
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}success={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


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
    """
    - None / "" => None
    - "10,5" => 10.5
    - 0 => 0.0 (lo dejamos pasar; el service decide si lo acepta)
    """
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
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    ok = request.query_params.get("success")
    error = request.query_params.get("error")

    try:
        recepcion = obtener_recepcion_segura(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        lineas = listar_lineas_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        log_inbound_event(
            "lineas_lista_view",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            total_lineas=len(lineas),
        )

        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "lineas": lineas,
                "qs_success": ok,
                "qs_error": error,
            },
        )

    except InboundDomainError as e:
        log_inbound_error(
            "lineas_lista_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}", error=e.message)

    except Exception as e:
        log_inbound_error(
            "lineas_lista_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        raise


# ============================================================
# FORM NUEVA
# ============================================================

@router.get("/recepciones/{recepcion_id}/lineas/nueva", response_class=HTMLResponse)
async def inbound_nueva_linea_form(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
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
# CREAR LINEA (UI libre, reglas en service)
# ============================================================


@router.post("/recepciones/{recepcion_id}/lineas", response_class=HTMLResponse)
async def inbound_agregar_linea(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    # Producto
    producto_id: str = Form(""),
    nuevo_producto_nombre: str = Form(""),
    nuevo_producto_unidad_base: str = Form(""),
    # Campos línea
    lote: str = Form(""),
    fecha_vencimiento: str = Form(""),
    unidad: str = Form(""),
    observaciones: str = Form(""),
    bultos: str = Form(""),
    # Documento / objetivo
    cantidad_documento: str = Form(""),
    kilos: str = Form(""),
    # Lecturas / recibidos (opcional)
    cantidad_recibida: str = Form(""),
    temperatura_objetivo: str = Form(""),
    temperatura_recibida: str = Form(""),

    # ✅ NUEVO: Overrides conversión (solo estimados UI)
    peso_unitario_kg_override: str = Form(""),
    unidades_por_bulto_override: str = Form(""),
    peso_por_bulto_kg_override: str = Form(""),
    nombre_bulto_override: str = Form(""),
):
    negocio_id = user["negocio_id"]


    try:
        # ✅ Coherencia enterprise: si no es editable, no creamos
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
            # Producto “rápido” (enterprise-friendly: lo crea activo en el negocio)
            producto_obj = Producto(
                negocio_id=negocio_id,
                nombre=nuevo_nombre,
                unidad=(nuevo_unidad or "unidad"),
                activo=1,
            )
            db.add(producto_obj)
            db.flush()

        if not producto_obj:
            raise InboundDomainError("Debes seleccionar un producto o ingresar un producto rápido.")

        fecha_ven_dt = _parse_date_iso(fecha_vencimiento)

        cant_doc = _to_float_or_none(cantidad_documento)
        kg_doc = _to_float_or_none(kilos)

        cant_rec = _to_float_or_none(cantidad_recibida)
        temp_obj = _to_float_or_none(temperatura_objetivo)
        temp_rec = _to_float_or_none(temperatura_recibida)
        bultos_i = _to_int_or_none(bultos)

        # ✅ overrides parse
        pu_ov = _to_float_or_none(peso_unitario_kg_override)
        ub_ov = _to_int_or_none(unidades_por_bulto_override)
        pb_ov = _to_float_or_none(peso_por_bulto_kg_override)
        nb_ov = _to_str_or_none(nombre_bulto_override)

        linea = crear_linea_inbound(
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

            # ✅ overrides
            peso_unitario_kg_override=pu_ov,
            unidades_por_bulto_override=ub_ov,
            peso_por_bulto_kg_override=pb_ov,
            nombre_bulto_override=nb_ov,
        )

        registrar_auditoria(
            db=db,
            user=user,
            accion="INBOUND_AGREGAR_LINEA",
            detalle={
                "recepcion_id": recepcion_id,
                "linea_id": linea.id,
                "producto_id": producto_obj.id,
            },
        )

        log_inbound_event(
            "linea_creada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            linea_id=linea.id,
            producto_id=producto_obj.id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", ok="Línea creada.")

    except InboundDomainError as e:
        log_inbound_error(
            "linea_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas/nueva", error=e.message)

    except Exception as e:
        log_inbound_error(
            "linea_crear_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas/nueva",
            error="Error inesperado al crear línea. Revisa logs.",
        )


# ============================================================
# ELIMINAR
# ============================================================

@router.post("/recepciones/{recepcion_id}/lineas/{linea_id}/eliminar", response_class=HTMLResponse)
async def inbound_eliminar_linea(
    recepcion_id: int,
    linea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

        eliminar_linea_inbound(db=db, negocio_id=negocio_id, linea_id=linea_id)

        registrar_auditoria(
            db=db,
            user=user,
            accion="INBOUND_ELIMINAR_LINEA",
            detalle={"recepcion_id": recepcion_id, "linea_id": linea_id},
        )

        log_inbound_event(
            "linea_eliminada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            linea_id=linea_id,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", ok="Línea eliminada.")

    except InboundDomainError as e:
        log_inbound_error(
            "linea_eliminar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            linea_id=linea_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", error=e.message)

    except Exception as e:
        log_inbound_error(
            "linea_eliminar_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            linea_id=linea_id,
            error=str(e),
        )
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas",
            error="Error inesperado al eliminar línea. Revisa logs.",
        )


# ============================================================
# RECONCILIAR (BOTÓN)
# ============================================================

@router.post("/recepciones/{recepcion_id}/lineas/reconciliar", response_class=HTMLResponse)
async def inbound_lineas_reconciliar(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        # valida multi-tenant + existencia
        _ = obtener_recepcion_segura(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        resumen = reconciliar_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        msg = (
            f"Reconciliación OK · líneas {resumen.get('lineas_actualizadas', 0)}/{resumen.get('lineas_total', 0)}"
            f" · kg {resumen.get('total_peso_kg', 0)} · cant {resumen.get('total_cantidad', 0)}"
        )

        log_inbound_event(
            "lineas_reconciliadas",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            summary=resumen,
        )

        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", ok=msg)

    except InboundDomainError as e:
        log_inbound_error(
            "lineas_reconciliar_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=e.message,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}/lineas", error=e.message)

    except Exception as e:
        log_inbound_error(
            "lineas_reconciliar_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/lineas",
            error="Error inesperado al reconciliar. Revisa logs.",
        )
