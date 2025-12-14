# modules/inbound_orbion/routes/routes_inbound_analytics.py
"""
Rutas de analítica / métricas – Inbound ORBION

✔ UI: /inbound/analytics
✔ API: /inbound/metrics/*
✔ Restricciones por plan (analytics + dataset ML)
✔ Logging estructurado inbound.*
✔ Validación robusta de fechas y multi-tenant

Notas enterprise:
- Evitamos importar modelos no usados.
- Normalizamos y validamos rangos de fecha.
- Ordenamientos consistentes (proveedores por peor promedio / mayor demora).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundConfig, InboundRecepcion
from core.plans import get_inbound_plan_config

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    calcular_metricas_negocio,
    calcular_metricas_recepcion,
)
from modules.inbound_orbion.services.services_inbound_config import (
    check_inbound_ml_dataset_permission,
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

def _parse_iso_date_param(value: Optional[str], param_name: str) -> Optional[datetime]:
    """
    Acepta:
      - YYYY-MM-DD
      - ISO con hora
      - ISO con timezone

    Retorna datetime timezone-aware en UTC (para filtros consistentes).
    """
    if not value:
        return None

    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Parámetro '{param_name}' inválido (usar YYYY-MM-DD o ISO 8601).",
        ) from exc

    # Si viene naive -> asumimos UTC (consistencia backend)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serializar_recepcion(r: InboundRecepcion, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.id,
        "codigo": r.codigo,
        "proveedor": getattr(r, "proveedor", None) or "Sin proveedor",
        "estado": r.estado,
        "creado_en": r.creado_en.isoformat() if r.creado_en else None,
        "tiempo_espera_min": metrics.get("tiempo_espera_min"),
        "tiempo_descarga_min": metrics.get("tiempo_descarga_min"),
        "tiempo_total_min": metrics.get("tiempo_total_min"),
        "incidencias": len(r.incidencias) if getattr(r, "incidencias", None) is not None else 0,
        "creado_en_fmt": r.creado_en.strftime("%d-%m-%Y %H:%M") if r.creado_en else None,
    }


# ============================
#   UI ANALYTICS
# ============================

@router.get("/analytics", response_class=HTMLResponse)
async def inbound_analytics(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    # Restricción por plan
    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)
    if not bool(plan_cfg.get("enable_inbound_analytics", False)):
        log_inbound_error(
            "analytics_plan_denied",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plan=negocio.plan_tipo,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "Tu plan actual no incluye analítica avanzada de inbound. "
                "Contacta a soporte para actualizar tu plan."
            ),
        )

    ahora = _utcnow()
    hace_30 = ahora - timedelta(days=30)

    resumen = calcular_metricas_negocio(
        db=db,
        negocio_id=negocio_id,
        desde=hace_30,
        hasta=None,
    )

    recepciones = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.negocio_id == negocio_id,
            InboundRecepcion.creado_en >= hace_30,
        )
        .order_by(InboundRecepcion.creado_en.desc())
        .limit(50)
        .all()
    )

    recepciones_data: list[dict[str, Any]] = []
    estados_count: dict[str, int] = defaultdict(int)
    proveedores_data: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "tiempos_totales": []})

    for r in recepciones:
        m = calcular_metricas_recepcion(r)
        estados_count[r.estado] += 1

        prov = getattr(r, "proveedor", None) or "Sin proveedor"
        proveedores_data[prov]["total"] += 1
        if m.get("tiempo_total_min") is not None:
            proveedores_data[prov]["tiempos_totales"].append(m["tiempo_total_min"])

        recepciones_data.append(_serializar_recepcion(r, m))

    # Proveedores: promedio tiempo total
    proveedores_resumen: list[dict[str, Any]] = []
    for prov, info in proveedores_data.items():
        tiempos = info["tiempos_totales"]
        avg_total = (sum(tiempos) / len(tiempos)) if tiempos else None
        proveedores_resumen.append(
            {
                "proveedor": prov,
                "total_recepciones": info["total"],
                "promedio_tiempo_total_min": avg_total,
            }
        )

    # Ordenar: primero los que tienen promedio (desc) y al final los None
    proveedores_resumen.sort(
        key=lambda x: (x["promedio_tiempo_total_min"] is None, -(x["promedio_tiempo_total_min"] or 0.0)),
    )

    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )

    log_inbound_event(
        "analytics_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total_recepciones=len(recepciones),
    )

    return templates.TemplateResponse(
        "inbound_analytics.html",
        {
            "request": request,
            "user": user,
            "modulo_nombre": "Orbion Inbound",
            "resumen": resumen,
            "recepciones": recepciones_data,
            "estados_count": dict(estados_count),
            "proveedores_resumen": proveedores_resumen,
            "desde": hace_30,
            "hasta": ahora,
            "config": config,
        },
    )


# ============================
#   API METRICS
# ============================

@router.get("/metrics/resumen", response_class=JSONResponse)
async def inbound_metrics_resumen(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
):
    negocio_id = user["negocio_id"]

    dt_desde = None
    dt_hasta = None

    try:
        dt_desde = _parse_iso_date_param(desde, "desde")
        dt_hasta = _parse_iso_date_param(hasta, "hasta")
    except HTTPException as exc:
        log_inbound_error(
            "metrics_resumen_param_invalido",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            desde=desde,
            hasta=hasta,
            error=str(exc.detail),
        )
        raise

    if dt_desde and dt_hasta and dt_desde > dt_hasta:
        log_inbound_error(
            "metrics_resumen_rango_invalido",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            desde=desde,
            hasta=hasta,
        )
        raise HTTPException(status_code=400, detail="Rango inválido: 'desde' no puede ser mayor que 'hasta'.")

    metrics = calcular_metricas_negocio(
        db=db,
        negocio_id=negocio_id,
        desde=dt_desde,
        hasta=dt_hasta,
    )

    log_inbound_event(
        "metrics_resumen_api",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        desde=desde,
        hasta=hasta,
    )

    return {
        "negocio_id": negocio_id,
        "desde": dt_desde.isoformat() if dt_desde else None,
        "hasta": dt_hasta.isoformat() if dt_hasta else None,
        **metrics,
    }


@router.get("/metrics/dataset", response_class=JSONResponse)
async def inbound_metrics_dataset(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    # Restricción por plan para dataset avanzado ML/IA
    try:
        check_inbound_ml_dataset_permission(negocio)
    except InboundDomainError as e:
        log_inbound_error(
            "metrics_dataset_plan_denied",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=getattr(e, "message", str(e)),
        )
        raise HTTPException(status_code=403, detail=getattr(e, "message", str(e)))

    dt_desde = None
    dt_hasta = None
    try:
        dt_desde = _parse_iso_date_param(desde, "desde")
        dt_hasta = _parse_iso_date_param(hasta, "hasta")
    except HTTPException as exc:
        log_inbound_error(
            "metrics_dataset_param_invalido",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            desde=desde,
            hasta=hasta,
            error=str(exc.detail),
        )
        raise

    if dt_desde and dt_hasta and dt_desde > dt_hasta:
        raise HTTPException(status_code=400, detail="Rango inválido: 'desde' no puede ser mayor que 'hasta'.")

    q = db.query(InboundRecepcion).filter(InboundRecepcion.negocio_id == negocio_id)

    if dt_desde:
        q = q.filter(InboundRecepcion.creado_en >= dt_desde)
    if dt_hasta:
        q = q.filter(InboundRecepcion.creado_en <= dt_hasta)

    recepciones = q.all()

    data: list[dict[str, Any]] = []
    for r in recepciones:
        m = calcular_metricas_recepcion(r)
        data.append(
            {
                "inbound_id": r.id,
                "codigo": r.codigo,
                "proveedor": getattr(r, "proveedor", None),
                "tipo_carga": getattr(r, "tipo_carga", None),
                "estado": r.estado,
                "creado_en": r.creado_en.isoformat() if r.creado_en else None,
                "fecha_arribo": r.fecha_arribo.isoformat() if r.fecha_arribo else None,
                "fecha_inicio_descarga": r.fecha_inicio_descarga.isoformat() if r.fecha_inicio_descarga else None,
                "fecha_fin_descarga": r.fecha_fin_descarga.isoformat() if r.fecha_fin_descarga else None,
                "cantidad_lineas": len(getattr(r, "lineas", []) or []),
                "cantidad_incidencias": len(getattr(r, "incidencias", []) or []),
                "tiempo_espera_min": m.get("tiempo_espera_min"),
                "tiempo_descarga_min": m.get("tiempo_descarga_min"),
                "tiempo_total_min": m.get("tiempo_total_min"),
            }
        )

    log_inbound_event(
        "metrics_dataset_api",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        desde=desde,
        hasta=hasta,
        registros=len(data),
    )

    return {
        "negocio_id": negocio_id,
        "cantidad_registros": len(data),
        "data": data,
    }


@router.get("/recepciones/{recepcion_id}/metrics", response_class=JSONResponse)
async def inbound_metrics_recepcion(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    """
    Métricas detalladas para UNA recepción específica.
    Ruta final: /inbound/recepciones/{recepcion_id}/metrics
    """
    negocio_id = user["negocio_id"]

    recepcion = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.id == recepcion_id,
            InboundRecepcion.negocio_id == negocio_id,
        )
        .first()
    )

    if not recepcion:
        log_inbound_error(
            "metrics_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user.get("email"),
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    metrics = calcular_metricas_recepcion(recepcion)

    log_inbound_event(
        "metrics_recepcion_api",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user.get("email"),
    )

    return {
        "inbound_id": recepcion.id,
        "codigo": recepcion.codigo,
        "proveedor": getattr(recepcion, "proveedor", None),
        "estado": recepcion.estado,
        "creado_en": recepcion.creado_en.isoformat() if recepcion.creado_en else None,
        "fecha_arribo": recepcion.fecha_arribo.isoformat() if recepcion.fecha_arribo else None,
        "fecha_inicio_descarga": recepcion.fecha_inicio_descarga.isoformat() if recepcion.fecha_inicio_descarga else None,
        "fecha_fin_descarga": recepcion.fecha_fin_descarga.isoformat() if recepcion.fecha_fin_descarga else None,
        "metrics": metrics,
    }
