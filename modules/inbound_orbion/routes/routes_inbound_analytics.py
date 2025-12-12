# modules/inbound_orbion/routes/routes_inbound_analytics.py

from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import (
    APIRouter,
    Request,
    Depends,
    HTTPException,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, InboundConfig
from core.plans import get_inbound_plan_config

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    calcular_metricas_recepcion,
    calcular_metricas_negocio,
)
from modules.inbound_orbion.services.services_inbound_config import (
    check_inbound_ml_dataset_permission,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ============================
#   ANALÍTICA / MÉTRICAS
# ============================

@router.get("/analytics", response_class=HTMLResponse)
async def inbound_analytics(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    # Restricción por plan para analítica inbound
    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)
    if not plan_cfg.get("enable_inbound_analytics", False):
        log_inbound_error(
            "analytics_plan_denied",
            negocio_id=negocio_id,
            user_email=user["email"],
            plan=negocio.plan_tipo,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "Tu plan actual no incluye analítica avanzada de inbound. "
                "Contacta a soporte para actualizar tu plan."
            ),
        )

    ahora = datetime.utcnow()
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

    recepciones_data = []
    estados_count = defaultdict(int)
    proveedores_data = defaultdict(lambda: {"total": 0, "tiempos_totales": []})

    for r in recepciones:
        m = calcular_metricas_recepcion(r)
        estados_count[r.estado] += 1

        prov = r.proveedor or "Sin proveedor"
        proveedores_data[prov]["total"] += 1
        if m["tiempo_total_min"] is not None:
            proveedores_data[prov]["tiempos_totales"].append(m["tiempo_total_min"])

        recepciones_data.append(
            {
                "id": r.id,
                "codigo": r.codigo,
                "proveedor": r.proveedor,
                "estado": r.estado,
                "creado_en": r.creado_en,
                "tiempo_espera_min": m["tiempo_espera_min"],
                "tiempo_descarga_min": m["tiempo_descarga_min"],
                "tiempo_total_min": m["tiempo_total_min"],
                "incidencias": len(r.incidencias),
            }
        )

    proveedores_resumen = []
    for prov, info in proveedores_data.items():
        tiempos = info["tiempos_totales"]
        avg_total = sum(tiempos) / len(tiempos) if tiempos else None
        proveedores_resumen.append(
            {
                "proveedor": prov,
                "total_recepciones": info["total"],
                "promedio_tiempo_total_min": avg_total,
            }
        )

    proveedores_resumen.sort(
        key=lambda x: (x["promedio_tiempo_total_min"] is None, x["promedio_tiempo_total_min"] or 0),
        reverse=True,
    )

    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )

    log_inbound_event(
        "analytics_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        total_recepciones=len(recepciones),
        resumen=resumen,
    )

    return templates.TemplateResponse(
        "inbound_analytics.html",
        {
            "request": request,
            "user": user,
            "modulo_nombre": "Orbion Inbound",
            "resumen": resumen,
            "recepciones": recepciones_data,
            "estados_count": estados_count,
            "proveedores_resumen": proveedores_resumen,
            "desde": hace_30,
            "hasta": ahora,
            "config": config,
        },
    )


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

    if desde:
        try:
            dt_desde = datetime.fromisoformat(desde)
        except ValueError as e:
            log_inbound_error(
                "metrics_resumen_desde_invalido",
                negocio_id=negocio_id,
                user_email=user["email"],
                raw_value=desde,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail="Parámetro 'desde' inválido (usar YYYY-MM-DD).")

    if hasta:
        try:
            dt_hasta = datetime.fromisoformat(hasta)
        except ValueError as e:
            log_inbound_error(
                "metrics_resumen_hasta_invalido",
                negocio_id=negocio_id,
                user_email=user["email"],
                raw_value=hasta,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail="Parámetro 'hasta' inválido (usar YYYY-MM-DD).")

    metrics = calcular_metricas_negocio(
        db=db,
        negocio_id=negocio_id,
        desde=dt_desde,
        hasta=dt_hasta,
    )

    log_inbound_event(
        "metrics_resumen_api",
        negocio_id=negocio_id,
        user_email=user["email"],
        desde=desde,
        hasta=hasta,
        metrics=metrics,
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
            user_email=user["email"],
            error=e.message,
        )
        raise HTTPException(status_code=403, detail=e.message)

    dt_desde = None
    dt_hasta = None

    if desde:
        try:
            dt_desde = datetime.fromisoformat(desde)
        except ValueError as e:
            log_inbound_error(
                "metrics_dataset_desde_invalido",
                negocio_id=negocio_id,
                user_email=user["email"],
                raw_value=desde,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail="Parámetro 'desde' inválido (usar YYYY-MM-DD).")

    if hasta:
        try:
            dt_hasta = datetime.fromisoformat(hasta)
        except ValueError as e:
            log_inbound_error(
                "metrics_dataset_hasta_invalido",
                negocio_id=negocio_id,
                user_email=user["email"],
                raw_value=hasta,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail="Parámetro 'hasta' inválido (usar YYYY-MM-DD).")

    q = db.query(InboundRecepcion).filter(
        InboundRecepcion.negocio_id == negocio_id,
    )

    if dt_desde:
        q = q.filter(InboundRecepcion.creado_en >= dt_desde)
    if dt_hasta:
        q = q.filter(InboundRecepcion.creado_en <= dt_hasta)

    recepciones = q.all()

    data = []
    for r in recepciones:
        m = calcular_metricas_recepcion(r)
        record = {
            "inbound_id": r.id,
            "codigo": r.codigo,
            "proveedor": r.proveedor,
            "tipo_carga": r.tipo_carga,
            "estado": r.estado,
            "creado_en": r.creado_en.isoformat() if r.creado_en else None,
            "fecha_arribo": r.fecha_arribo.isoformat() if r.fecha_arribo else None,
            "fecha_inicio_descarga": r.fecha_inicio_descarga.isoformat() if r.fecha_inicio_descarga else None,
            "fecha_fin_descarga": r.fecha_fin_descarga.isoformat() if r.fecha_fin_descarga else None,
            "cantidad_lineas": len(r.lineas),
            "cantidad_incidencias": len(r.incidencias),
            "tiempo_espera_min": m["tiempo_espera_min"],
            "tiempo_descarga_min": m["tiempo_descarga_min"],
            "tiempo_total_min": m["tiempo_total_min"],
        }
        data.append(record)

    log_inbound_event(
        "metrics_dataset_api",
        negocio_id=negocio_id,
        user_email=user["email"],
        desde=desde,
        hasta=hasta,
        registros=len(data),
    )

    return {
        "negocio_id": negocio_id,
        "cantidad_registros": len(data),
        "data": data,
    }


@router.get("/{recepcion_id}/metrics", response_class=JSONResponse)
async def inbound_metrics_recepcion(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
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
            user_email=user["email"],
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    metrics = calcular_metricas_recepcion(recepcion)

    log_inbound_event(
        "metrics_recepcion_api",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        metrics=metrics,
    )

    return {
        "inbound_id": recepcion.id,
        "codigo": recepcion.codigo,
        "proveedor": recepcion.proveedor,
        "estado": recepcion.estado,
        "creado_en": recepcion.creado_en.isoformat() if recepcion.creado_en else None,
        "fecha_arribo": recepcion.fecha_arribo.isoformat() if recepcion.fecha_arribo else None,
        "fecha_inicio_descarga": recepcion.fecha_inicio_descarga.isoformat() if recepcion.fecha_inicio_descarga else None,
        "fecha_fin_descarga": recepcion.fecha_fin_descarga.isoformat() if recepcion.fecha_fin_descarga else None,
        "metrics": metrics,
    }
