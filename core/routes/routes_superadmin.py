"""
Superadmin Console – ORBION (SaaS enterprise)

✔ Dashboard global (solo superadmin global)
✔ Gestión de negocios (lista + detalle)
✔ Update plan/estado (legacy)
✔ Alertas globales
✔ Impersonación ("ver como negocio") + salir modo negocio
✔ Auditoría por negocio con filtros + paginación (enterprise)
✔ Job manual tipo cron (renovación de suscripciones SaaS)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_

from core.database import get_db
from core.logging_config import logger
from core.models import Negocio, Producto, Movimiento, Alerta, Usuario, Auditoria
from core.security import (
    require_superadmin_dep,
    _get_session_payload_from_request,
    _set_session_cookie_from_payload,
)
from core.plans import PLANES_CORE_WMS, normalize_plan
from core.web import templates

# 🔑 SaaS
from core.services.services_renewal_job import run_subscription_renewal_job


# ============================
# ROUTER
# ============================

router = APIRouter(
    prefix="/superadmin",
    tags=["superadmin"],
)


# ============================
# DASHBOARD
# ============================

@router.get("/dashboard", response_class=HTMLResponse)
async def superadmin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    # Si el superadmin está impersonando, lo mandamos al negocio
    if user.get("impersonando_negocio_id"):
        return RedirectResponse(url="/dashboard", status_code=302)

    total_negocios = db.query(Negocio).count()
    negocios_activos = db.query(Negocio).filter(Negocio.estado == "activo").count()
    negocios_suspendidos = db.query(Negocio).filter(Negocio.estado == "suspendido").count()
    alertas_pendientes = db.query(Alerta).filter(Alerta.estado == "pendiente").count()

    logger.info("[SUPERADMIN] dashboard total_negocios=%s", total_negocios)

    return templates.TemplateResponse(
        "app/superadmin_dashboard.html",
        {
            "request": request,
            "user": user,
            "total_negocios": total_negocios,
            "negocios_activos": negocios_activos,
            "negocios_suspendidos": negocios_suspendidos,
            "alertas_pendientes": alertas_pendientes,
        },
    )


# ============================
# NEGOCIOS LISTA
# ============================

@router.get("/negocios", response_class=HTMLResponse)
async def superadmin_negocios(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocios = db.query(Negocio).order_by(Negocio.id.desc()).all()
    data: list[dict] = []

    hace_30 = datetime.utcnow() - timedelta(days=30)

    for n in negocios:
        usuarios = db.query(Usuario).filter(Usuario.negocio_id == n.id).count()
        productos = db.query(Producto).filter(Producto.negocio_id == n.id).count()

        movimientos = (
            db.query(Movimiento)
            .filter(
                Movimiento.negocio_id == n.id,
                Movimiento.fecha >= hace_30,
            )
            .count()
        )

        data.append(
            {
                "id": n.id,
                "nombre": n.nombre_fantasia,
                "plan": n.plan_tipo,
                "estado": n.estado,
                "usuarios": usuarios,
                "productos": productos,
                "movimientos_30d": movimientos,
                "ultimo_acceso": n.ultimo_acceso,
            }
        )

    return templates.TemplateResponse(
        "app/superadmin_negocios.html",
        {
            "request": request,
            "user": user,
            "negocios": data,
        },
    )


# ============================
# NEGOCIO DETALLE
# ============================

@router.get("/negocios/{negocio_id}", response_class=HTMLResponse)
async def superadmin_negocio_detalle(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    eventos = (
        db.query(Auditoria)
        .filter(Auditoria.negocio_id == negocio_id)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(5)
        .all()
    )

    return templates.TemplateResponse(
        "app/superadmin_negocio_detalle.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "planes": list(PLANES_CORE_WMS.keys()),
            "eventos_auditoria": eventos,
        },
    )


@router.post("/negocios/{negocio_id}/update")
async def superadmin_negocio_update(
    request: Request,
    negocio_id: int,
    plan_tipo: str = Form(...),
    estado: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    plan_tipo_norm = normalize_plan(plan_tipo)
    if plan_tipo_norm not in PLANES_CORE_WMS:
        raise HTTPException(status_code=400, detail="Plan inválido.")

    estado_norm = (estado or "").strip().lower()
    if estado_norm not in {"activo", "suspendido"}:
        raise HTTPException(status_code=400, detail="Estado inválido.")

    negocio.plan_tipo = plan_tipo_norm
    negocio.estado = estado_norm
    db.commit()

    logger.info(
        "[SUPERADMIN] update negocio_id=%s plan=%s estado=%s",
        negocio_id,
        plan_tipo_norm,
        estado_norm,
    )

    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=302)


# ============================
# ALERTAS GLOBALES
# ============================

@router.get("/alertas", response_class=HTMLResponse)
async def superadmin_alertas(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    alertas = (
        db.query(Alerta)
        .join(Negocio, Alerta.negocio_id == Negocio.id)
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
        .limit(500)
        .all()
    )

    return templates.TemplateResponse(
        "app/superadmin_alertas.html",
        {
            "request": request,
            "user": user,
            "alertas": alertas,
        },
    )


# ============================
# IMPERSONACIÓN
# ============================

@router.get("/negocios/{negocio_id}/ver-como")
async def superadmin_ver_como_negocio(
    negocio_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado.")

    payload = _get_session_payload_from_request(request)
    if not payload:
        return RedirectResponse("/app/login", status_code=302)

    payload["acting_negocio_id"] = negocio.id
    payload["acting_negocio_nombre"] = negocio.nombre_fantasia

    resp = RedirectResponse(url="/dashboard", status_code=302)
    _set_session_cookie_from_payload(resp, payload)

    logger.info("[SUPERADMIN] impersonate negocio_id=%s", negocio.id)
    return resp


@router.get("/salir-modo-negocio")
async def superadmin_salir_modo_negocio(
    request: Request,
    user: dict = Depends(require_superadmin_dep),
):
    payload = _get_session_payload_from_request(request)
    if not payload:
        return RedirectResponse("/app/login", status_code=302)

    payload.pop("acting_negocio_id", None)
    payload.pop("acting_negocio_nombre", None)

    resp = RedirectResponse(url="/superadmin/dashboard", status_code=302)
    _set_session_cookie_from_payload(resp, payload)

    logger.info("[SUPERADMIN] exit_impersonation")
    return resp


# ============================
# AUDITORÍA (PAGINADA + FILTROS)
# ============================

@router.get("/negocios/{negocio_id}/auditoria", response_class=HTMLResponse)
async def superadmin_auditoria_negocio(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    params = request.query_params

    texto = (params.get("q") or "").strip()
    fecha_desde_str = (params.get("desde") or "").strip()
    fecha_hasta_str = (params.get("hasta") or "").strip()
    nivel_str = (params.get("nivel") or "").strip()
    page_str = (params.get("page") or "").strip()

    try:
        page = int(page_str) if page_str else 1
    except ValueError:
        page = 1
    if page < 1:
        page = 1

    PAGE_SIZE = 10

    fecha_desde = None
    fecha_hasta = None

    if fecha_desde_str:
        try:
            fecha_desde = datetime.strptime(fecha_desde_str, "%Y-%m-%d")
        except ValueError:
            fecha_desde = None

    if fecha_hasta_str:
        try:
            fecha_hasta = datetime.strptime(fecha_hasta_str, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            fecha_hasta = None

    base_query = db.query(Auditoria).filter(Auditoria.negocio_id == negocio_id)

    if fecha_desde:
        base_query = base_query.filter(Auditoria.fecha >= fecha_desde)
    if fecha_hasta:
        base_query = base_query.filter(Auditoria.fecha <= fecha_hasta)

    if texto:
        like_expr = f"%{texto}%"
        base_query = base_query.filter(
            or_(
                Auditoria.usuario.ilike(like_expr),
                Auditoria.accion.ilike(like_expr),
                Auditoria.detalle.ilike(like_expr),
            )
        )

    total_filtrado = base_query.count()
    total_pages = max(1, math.ceil(total_filtrado / PAGE_SIZE))
    if page > total_pages:
        page = total_pages

    registros_db = (
        base_query
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    registros: list[Auditoria] = []
    for r in registros_db:
        nivel = clasificar_evento_auditoria(r.accion, r.detalle)
        setattr(r, "nivel", nivel)
        registros.append(r)

    if nivel_str in {"critico", "warning", "info", "normal"}:
        registros = [r for r in registros if getattr(r, "nivel", "normal") == nivel_str]

    paginacion = {
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total_filtrado,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
    }

    return templates.TemplateResponse(
        "app/superadmin_auditoria.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "registros": registros,
            "filtros": {
                "q": texto,
                "desde": fecha_desde_str,
                "hasta": fecha_hasta_str,
                "nivel": nivel_str,
            },
            "paginacion": paginacion,
        },
    )


# ============================
# HELPERS
# ============================

def clasificar_evento_auditoria(accion: str, detalle: str | None = None) -> str:
    a = (accion or "").lower()

    if a in {
        "negocio_suspendido",
        "negocio_reactivado",
        "usuario_eliminado",
        "producto_eliminado",
        "stock_borrado_masivo",
        "intento_login_fallido",
    }:
        return "critico"

    if a in {
        "salida_merma",
        "stock_critico",
        "alerta_creada",
        "producto_modificado",
        "usuario_bloqueado",
    }:
        return "warning"

    if a in {
        "login_ok",
        "logout",
        "producto_creado",
        "entrada_creada",
        "salida_creada",
        "usuario_creado",
    }:
        return "info"

    return "normal"


# ============================
# JOB: RENOVAR SUSCRIPCIONES (CRON MANUAL)
# ============================

@router.post("/renew-subscriptions", response_class=HTMLResponse)
async def superadmin_job_renew_subscriptions(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    """
    Job manual de renovación de suscripciones SaaS.
    Equivalente a cron mensual.
    """

    if user.get("impersonando_negocio_id"):
        raise HTTPException(status_code=403, detail="No permitido en modo negocio")

    res = run_subscription_renewal_job(db)

    logger.info(
        "[SUPERADMIN][JOB] renew_subscriptions checked=%s renewed=%s cancelled=%s errors=%s",
        res.checked,
        res.renewed,
        res.cancelled,
        res.errors,
    )

    return templates.TemplateResponse(
        "app/superadmin_job_result.html",
        {
            "request": request,
            "user": user,
            "job_name": "Renovación de suscripciones",
            "result": res,
        },
    )
