# modules/inbound_orbion/routes/routes_inbound_recepciones.py
from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus
from typing import Any

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import select

from core.database import get_db
from core.models.inbound.proveedores import Proveedor
from core.models.inbound.recepciones import InboundRecepcion

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from modules.inbound_orbion.services.services_inbound_recepciones import (
    listar_recepciones,
    crear_recepcion,
    obtener_recepcion,
    actualizar_recepcion,
)
from modules.inbound_orbion.services.services_inbound_recepcion_estados import (
    aplicar_accion_estado,
    obtener_metrics_recepcion,
)

# ✅ Enterprise: reconciliación desde pallets (contrato oficial vive en el service)
from modules.inbound_orbion.services.services_inbound_reconciliacion import (
    reconciliar_recepcion,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================================================
# Helpers enterprise (UX sin bloquear UI)
# ============================================================

def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    """
    Redirect con mensajes por querystring:
      - ?ok=...
      - ?error=...
    Nota: No bloquea UI. Solo informa.
    """
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _negocio_id_from_user(user: dict) -> int:
    negocio_id = user.get("negocio_id")
    if not negocio_id:
        raise InboundDomainError("No se encontró negocio_id en la sesión.")
    return int(negocio_id)


def _parse_date_iso(v: str | None) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _listar_proveedores(db: Session, negocio_id: int) -> list[Proveedor]:
    stmt = (
        select(Proveedor)
        .where(Proveedor.negocio_id == negocio_id)
        .where(Proveedor.activo == 1)
        .order_by(Proveedor.nombre.asc())
    )
    return list(db.execute(stmt).scalars().all())


def _safe_str(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    return s or None


# ============================================================
# LISTA / DASHBOARD
# ============================================================

@router.get("/recepciones", response_class=HTMLResponse)
async def inbound_recepciones_lista(
    request: Request,
    q: str | None = None,
    estado: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    # Plan flags (UI)
    inbound_plan = get_inbound_plan_config(user.get("plan_tipo"))
    inbound_analytics_enabled = bool(inbound_plan.get("enable_inbound_analytics", False))

    d_desde = _parse_date_iso(desde)
    d_hasta = _parse_date_iso(hasta)

    # Service = source of truth para filtros/tenant
    recepciones = listar_recepciones(
        db=db,
        negocio_id=negocio_id,
        q=_safe_str(q),
        estado=_safe_str(estado),
        desde=d_desde,
        hasta=d_hasta,
        limit=80,
    )

    # Refuerzo enterprise: preload proveedor (evita lazy inconsistencias en template)
    if recepciones:
        ids = [r.id for r in recepciones]
        stmt = (
            select(InboundRecepcion)
            .options(joinedload(InboundRecepcion.proveedor))
            .where(InboundRecepcion.negocio_id == negocio_id)
            .where(InboundRecepcion.id.in_(ids))
            .order_by(InboundRecepcion.created_at.desc())
        )
        recepciones = list(db.execute(stmt).scalars().all())

    log_inbound_event(
        "recepciones_dashboard_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        filtros={"q": q or "", "estado": estado or "", "desde": desde or "", "hasta": hasta or ""},
        total=len(recepciones),
    )

    return templates.TemplateResponse(
        "inbound_recepciones_dashboard.html",
        {
            "request": request,
            "user": user,
            "recepciones": recepciones,
            "filtros": {
                "q": q or "",
                "estado": estado or "",
                "desde": desde or "",
                "hasta": hasta or "",
            },
            "inbound_analytics_enabled": inbound_analytics_enabled,
            "modulo_nombre": "Orbion Inbound",
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )


# ============================================================
# CREAR (FORM)
# ============================================================

@router.get("/recepciones/nueva", response_class=HTMLResponse)
async def inbound_recepcion_nueva_form(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    proveedores = _listar_proveedores(db, negocio_id)

    log_inbound_event(
        "recepcion_create_form_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total_proveedores=len(proveedores),
    )

    return templates.TemplateResponse(
        "inbound_recepcion_form.html",
        {
            "request": request,
            "user": user,
            "mode": "create",
            "recepcion": None,
            "error": request.query_params.get("error"),
            "ok": request.query_params.get("ok"),
            "proveedores": proveedores,
            "form_action": "/inbound/recepciones/nueva",
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/recepciones/nueva", response_class=HTMLResponse)
async def inbound_recepcion_nueva_submit(
    request: Request,
    proveedor_id: int | None = Form(None),
    proveedor_nombre: str | None = Form(None),
    codigo_recepcion: str | None = Form(None),
    documento_ref: str | None = Form(None),
    contenedor: str | None = Form(None),
    patente_camion: str | None = Form(None),
    tipo_carga: str | None = Form(None),
    fecha_estimada_llegada: str | None = Form(None),
    fecha_recepcion: str | None = Form(None),
    observaciones: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        # ✅ Enterprise UX: permitimos ingresar todo, pero algunos mínimos se validan.
        # Mantengo "documento_ref" como mínimo documental (puedes relajar si quieres).
        if not (documento_ref or "").strip():
            raise InboundDomainError("Debes ingresar Documento Ref (guía/factura/BL/OC) para crear la recepción.")

        r = crear_recepcion(
            db=db,
            negocio_id=negocio_id,
            data={
                "proveedor_id": proveedor_id,
                "proveedor_nombre": proveedor_nombre,
                "codigo_recepcion": codigo_recepcion,
                "documento_ref": documento_ref,
                "contenedor": contenedor,
                "patente_camion": patente_camion,
                "tipo_carga": tipo_carga,
                "fecha_estimada_llegada": fecha_estimada_llegada,
                "fecha_recepcion": fecha_recepcion,
                "observaciones": observaciones,
                "estado": "PRE_REGISTRADO",
            },
        )

        log_inbound_event(
            "recepcion_creada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=r.id,
            codigo=getattr(r, "codigo_recepcion", None),
        )
        return _redirect(f"/inbound/recepciones/{r.id}", ok="Recepción creada.")

    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_create_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=None,
            error=str(e),
        )
        proveedores = _listar_proveedores(db, negocio_id)
        return templates.TemplateResponse(
            "inbound_recepcion_form.html",
            {
                "request": request,
                "user": user,
                "mode": "create",
                "recepcion": None,
                "error": str(e),
                "ok": None,
                "proveedores": proveedores,
                "form_action": "/inbound/recepciones/nueva",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=400,
        )

    except Exception as e:
        log_inbound_error(
            "recepcion_create_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=None,
            error=str(e),
        )
        proveedores = _listar_proveedores(db, negocio_id)
        return templates.TemplateResponse(
            "inbound_recepcion_form.html",
            {
                "request": request,
                "user": user,
                "mode": "create",
                "recepcion": None,
                "error": "Error inesperado al crear recepción. Revisa logs.",
                "ok": None,
                "proveedores": proveedores,
                "form_action": "/inbound/recepciones/nueva",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )


# ============================================================
# DETALLE
# ============================================================

@router.get("/recepciones/{recepcion_id}", response_class=HTMLResponse)
async def inbound_recepcion_detalle(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    try:
        # ✅ Enterprise eager-load (sin romper: usamos unique() por colecciones)
        stmt = (
            select(InboundRecepcion)
            .options(
                joinedload(InboundRecepcion.proveedor),
                selectinload(InboundRecepcion.lineas),
                selectinload(InboundRecepcion.incidencias),
                selectinload(InboundRecepcion.pallets),
            )
            .where(InboundRecepcion.id == recepcion_id)
            .where(InboundRecepcion.negocio_id == negocio_id)
        )

        r = db.execute(stmt).unique().scalar_one_or_none()
        if not r:
            raise InboundDomainError("Recepción no encontrada.")

        metrics = obtener_metrics_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        log_inbound_event(
            "recepcion_detalle_view",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
        )

        return templates.TemplateResponse(
            "inbound_recepcion_detalle.html",
            {
                "request": request,
                "user": user,
                "recepcion": r,
                "r": r,
                "metrics": metrics,
                "ok": ok,
                "error": error,
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_detail_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return templates.TemplateResponse(
            "inbound_recepcion_detalle.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "r": None,
                "metrics": None,
                "ok": None,
                "error": str(e),
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=404,
        )

    except Exception as e:
        log_inbound_error(
            "recepcion_detail_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return templates.TemplateResponse(
            "inbound_recepcion_detalle.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "r": None,
                "metrics": None,
                "ok": None,
                "error": "Error inesperado al abrir recepción. Revisa logs.",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )


# ============================================================
# RECONCILIAR (Recalcular pendientes / contrato)
# ============================================================

@router.post("/recepciones/{recepcion_id}/recalcular", response_class=HTMLResponse)
async def inbound_recepcion_recalcular(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        res: dict[str, Any] = reconciliar_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        msg = (
            f"Reconciliación OK · líneas: {res.get('lineas_actualizadas', 0)}/{res.get('lineas_total', 0)}"
            f" · kg: {res.get('total_peso_kg', 0)} · cant: {res.get('total_cantidad', 0)}"
        )

        log_inbound_event(
            "recepcion_reconciliada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            summary=res,
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}", ok=msg)

    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_reconciliar_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}", error=str(e))

    except Exception as e:
        log_inbound_error(
            "recepcion_reconciliar_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}",
            error="Error inesperado al recalcular. Revisa logs.",
        )


# ============================================================
# ESTADO (workflow)
# ============================================================

@router.post("/recepciones/{recepcion_id}/estado", response_class=HTMLResponse)
async def inbound_recepcion_estado_submit(
    request: Request,
    recepcion_id: int,
    accion: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        r = aplicar_accion_estado(db, negocio_id=negocio_id, recepcion_id=recepcion_id, accion=accion)

        log_inbound_event(
            "recepcion_estado_actualizado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=r.id,
            accion=accion,
        )
        return _redirect(f"/inbound/recepciones/{r.id}", ok=f"Estado actualizado: {accion}")

    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}", error=str(e))

    except Exception as e:
        log_inbound_error(
            "recepcion_estado_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        return _redirect(f"/inbound/recepciones/{recepcion_id}", error="Error inesperado al cambiar estado. Revisa logs.")


# ============================================================
# EDITAR
# ============================================================

@router.get("/recepciones/{recepcion_id}/editar", response_class=HTMLResponse)
async def inbound_recepcion_editar_form(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        r = obtener_recepcion(db, negocio_id, recepcion_id)
    except InboundDomainError as e:
        return _redirect("/inbound/recepciones", error=str(e))

    proveedores = _listar_proveedores(db, negocio_id)

    log_inbound_event(
        "recepcion_edit_form_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        recepcion_id=recepcion_id,
    )

    return templates.TemplateResponse(
        "inbound_recepcion_form.html",
        {
            "request": request,
            "user": user,
            "mode": "edit",
            "recepcion": r,
            "error": request.query_params.get("error"),
            "ok": request.query_params.get("ok"),
            "proveedores": proveedores,
            "form_action": f"/inbound/recepciones/{r.id}/editar",
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/recepciones/{recepcion_id}/editar", response_class=HTMLResponse)
async def inbound_recepcion_editar_submit(
    request: Request,
    recepcion_id: int,
    proveedor_id: int | None = Form(None),
    proveedor_nombre: str | None = Form(None),
    codigo_recepcion: str | None = Form(None),
    documento_ref: str | None = Form(None),
    contenedor: str | None = Form(None),
    patente_camion: str | None = Form(None),
    tipo_carga: str | None = Form(None),
    fecha_estimada_llegada: str | None = Form(None),
    fecha_recepcion: str | None = Form(None),
    observaciones: str | None = Form(None),
    estado: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        # ✅ Enterprise mínimo: no permitir dejar documento_ref vacío si llega en el form
        if documento_ref is not None and not documento_ref.strip():
            raise InboundDomainError("Documento Ref no puede quedar vacío.")

        r = actualizar_recepcion(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            data={
                "proveedor_id": proveedor_id,
                "proveedor_nombre": proveedor_nombre,
                "codigo_recepcion": codigo_recepcion,
                "documento_ref": documento_ref,
                "contenedor": contenedor,
                "patente_camion": patente_camion,
                "tipo_carga": tipo_carga,
                "fecha_estimada_llegada": fecha_estimada_llegada,
                "fecha_recepcion": fecha_recepcion,
                "observaciones": observaciones,
                "estado": estado,
            },
        )

        log_inbound_event(
            "recepcion_actualizada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=r.id,
        )
        return _redirect(f"/inbound/recepciones/{r.id}", ok="Recepción actualizada.")

    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_update_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        proveedores = _listar_proveedores(db, negocio_id)
        try:
            r_prev = obtener_recepcion(db, negocio_id, recepcion_id)
        except Exception:
            r_prev = None

        return templates.TemplateResponse(
            "inbound_recepcion_form.html",
            {
                "request": request,
                "user": user,
                "mode": "edit",
                "recepcion": r_prev,
                "error": str(e),
                "ok": None,
                "proveedores": proveedores,
                "form_action": f"/inbound/recepciones/{recepcion_id}/editar",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=400,
        )

    except Exception as e:
        log_inbound_error(
            "recepcion_update_unhandled",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            recepcion_id=recepcion_id,
            error=str(e),
        )
        proveedores = _listar_proveedores(db, negocio_id)
        try:
            r_prev = obtener_recepcion(db, negocio_id, recepcion_id)
        except Exception:
            r_prev = None

        return templates.TemplateResponse(
            "inbound_recepcion_form.html",
            {
                "request": request,
                "user": user,
                "mode": "edit",
                "recepcion": r_prev,
                "error": "Error inesperado al actualizar recepción. Revisa logs.",
                "ok": None,
                "proveedores": proveedores,
                "form_action": f"/inbound/recepciones/{recepcion_id}/editar",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )
