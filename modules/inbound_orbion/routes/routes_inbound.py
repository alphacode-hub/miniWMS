# modules/inbound_orbion/routes/routes_inbound.py

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict
import math

from fastapi import HTTPException, APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, Producto, InboundConfig, Negocio
from core.security import require_roles_dep
from core.services.services_audit import registrar_auditoria
from core.plans import get_inbound_plan_config

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    crear_linea_inbound,
    actualizar_linea_inbound,
    eliminar_linea_inbound,
    crear_incidencia_inbound,
    eliminar_incidencia_inbound,
    calcular_metricas_recepcion,
    calcular_metricas_negocio,
)
from modules.inbound_orbion.services.services_inbound_config import (
    check_inbound_recepcion_limit,
    check_inbound_ml_dataset_permission,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

# ============================
#   TEMPLATES
# ============================

HERE = Path(__file__).resolve()

PROJECT_ROOT = HERE.parents[3]   # .../miniWMS
INBOUND_TEMPLATES = HERE.parents[1] / "templates"  # modules/inbound_orbion/templates
GLOBAL_TEMPLATES = PROJECT_ROOT / "templates"      # templates/

templates = Jinja2Templates(
    directory=[
        str(GLOBAL_TEMPLATES),       # para base/base_app.html y otros html globales
        str(INBOUND_TEMPLATES),      # inbound_lista.html, inbound_detalle.html, etc.
    ]
)



router = APIRouter(
    prefix="/inbound",
    tags=["inbound"],
)


# ============================
#   HELPERS
# ============================

def _generar_codigo_inbound(db: Session, negocio_id: int) -> str:
    count = (
        db.query(InboundRecepcion)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .count()
    )
    numero = count + 1
    return f"INB-{datetime.now().year}-{numero:06d}"


def _get_negocio(db: Session, negocio_id: int) -> Negocio:
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return negocio


# ============================
#   LISTA / NUEVA RECEPCIÓN
# ============================

@router.get("/", response_class=HTMLResponse)
async def inbound_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    estado: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    page: int = 1,   # 👈 parámetro de página
):
    negocio_id = user["negocio_id"]
    negocio = _get_negocio(db, negocio_id)

    # Config de plan para habilitar o no el botón de analítica
    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)
    inbound_analytics_enabled = bool(plan_cfg.get("enable_inbound_analytics", False))

    # Query base
    q = (
        db.query(InboundRecepcion)
        .filter(InboundRecepcion.negocio_id == negocio_id)
    )

    # Filtro por estado
    if estado:
        q = q.filter(InboundRecepcion.estado == estado)

    # Filtros por fecha (creado_en)
    dt_desde = None
    dt_hasta = None

    if desde:
        try:
            dt_desde = datetime.fromisoformat(desde)
            q = q.filter(InboundRecepcion.creado_en >= dt_desde)
        except ValueError:
            dt_desde = None

    if hasta:
        try:
            # Hasta final del día
            dt_hasta = datetime.fromisoformat(hasta) + timedelta(days=1)
            q = q.filter(InboundRecepcion.creado_en < dt_hasta)
        except ValueError:
            dt_hasta = None

    # ============================
    #   PAGINACIÓN (5 POR PÁGINA)
    # ============================
    per_page = 5

    total_recepciones = q.count()
    total_pages = max(1, math.ceil(total_recepciones / per_page)) if total_recepciones > 0 else 1

    # Normalizar página
    if page < 1:
        page = 1
    if total_recepciones > 0 and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    recepciones = (
        q.order_by(InboundRecepcion.creado_en.desc())
         .offset(offset)
         .limit(per_page)
         .all()
    )

    filtros = {
        "estado": estado or "",
        "desde": desde or "",
        "hasta": hasta or "",
    }

    # Para construir los links de paginación manteniendo filtros
    qs_parts = []
    if estado:
        qs_parts.append(f"estado={estado}")
    if desde:
        qs_parts.append(f"desde={desde}")
    if hasta:
        qs_parts.append(f"hasta={hasta}")
    base_query = "&".join(qs_parts)  # ej: "estado=EN_ESPERA&desde=2025-12-01"

    log_inbound_event(
        "lista_recepciones_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        total_page=len(recepciones),
        total_filtered=total_recepciones,
        page=page,
        total_pages=total_pages,
        filtros=filtros,
    )

    return templates.TemplateResponse(
        "inbound_lista.html",
        {
            "request": request,
            "user": user,
            "recepciones": recepciones,
            "modulo_nombre": "Orbion Inbound",
            "filtros": filtros,
            "inbound_analytics_enabled": inbound_analytics_enabled,
            "page": page,
            "total_pages": total_pages,
            "total_recepciones": total_recepciones,
            "base_query": base_query,
        },
    )




@router.get("/nuevo", response_class=HTMLResponse)
async def inbound_nuevo_form(
    request: Request,
    user=Depends(require_roles_dep("admin", "operador")),
):
    log_inbound_event(
        "nueva_recepcion_form_view",
        negocio_id=user["negocio_id"],
        user_email=user["email"],
    )

    return templates.TemplateResponse(
        "inbound_form.html",
        {
            "request": request,
            "user": user,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/nuevo", response_class=HTMLResponse)
async def inbound_nuevo_submit(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    proveedor: str = Form(...),
    referencia_externa: str = Form(""),
    contenedor: str = Form(""),
    patente_camion: str = Form(""),
    tipo_carga: str = Form(""),
    fecha_estimada_llegada: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]
    negocio = _get_negocio(db, negocio_id)

    # Normalización de texto
    proveedor = proveedor.strip()
    referencia_externa = referencia_externa.strip() or None
    contenedor_norm = contenedor.strip().upper() or None
    patente_norm = patente_camion.strip().upper() or None
    tipo_carga = tipo_carga.strip() or None
    observaciones_norm = observaciones.strip() or None

    # Límite por plan (recepciones al mes)
    try:
        check_inbound_recepcion_limit(db, negocio)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_limit_reached",
            negocio_id=negocio_id,
            user_email=user["email"],
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    codigo = _generar_codigo_inbound(db, negocio_id)

    fecha_eta = None
    if fecha_estimada_llegada:
        try:
            fecha_eta = datetime.fromisoformat(fecha_estimada_llegada)
        except ValueError as e:
            log_inbound_error(
                "nueva_recepcion_fecha_eta_invalida",
                negocio_id=negocio_id,
                user_email=user["email"],
                raw_value=fecha_estimada_llegada,
                error=str(e),
            )
            raise HTTPException(
                status_code=400,
                detail="Fecha estimada de llegada inválida.",
            )

    recepcion = InboundRecepcion(
        negocio_id=negocio_id,
        codigo=codigo,
        proveedor=proveedor,
        referencia_externa=referencia_externa,
        contenedor=contenedor_norm,
        patente_camion=patente_norm,
        tipo_carga=tipo_carga,
        fecha_estimada_llegada=fecha_eta,
        observaciones=observaciones_norm,
        estado="PRE_REGISTRADO",
        creado_por_id=user["id"],
    )

    db.add(recepcion)
    db.commit()
    db.refresh(recepcion)

    registrar_auditoria(
        db=db,
        user=user,
        accion="CREAR_INBOUND",
        detalle={
            "mensaje": f"Se creó recepción inbound {recepcion.codigo}",
            "inbound_id": recepcion.id,
        },
    )

    log_inbound_event(
        "recepcion_creada",
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        user_email=user["email"],
        codigo=recepcion.codigo,
        proveedor=proveedor,
        estado=recepcion.estado,
    )

    return RedirectResponse(
        url="/inbound",
        status_code=302,
    )


# ============================
#   CONFIGURACIÓN POR NEGOCIO
# ============================

@router.get("/config", response_class=HTMLResponse)
async def inbound_config_view(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
):
    negocio_id = user["negocio_id"]
    negocio = _get_negocio(db, negocio_id)

    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )

    # Si no hay config, la creamos vacía (o con defaults si quisieras)
    if not config:
        config = InboundConfig(negocio_id=negocio_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    # Config del plan para mostrar límites de referencia (read-only)
    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)

    log_inbound_event(
        "config_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        plan=negocio.plan_tipo,
    )

    return templates.TemplateResponse(
        "inbound_config.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "config": config,
            "plan_cfg": plan_cfg,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/config", response_class=HTMLResponse)
async def inbound_config_save(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
):
    """
    Guarda la configuración inbound para el negocio actual.

    El formulario debe usar names que coincidan con
    los atributos del modelo InboundConfig.
    """
    negocio_id = user["negocio_id"]

    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )

    if not config:
        config = InboundConfig(negocio_id=negocio_id)
        db.add(config)
        db.flush()

    form = await request.form()

    def parse_value(raw: str):
        if raw is None:
            return None
        raw = str(raw).strip()
        if raw == "":
            return None

        lower = raw.lower()
        if lower in ("true", "on", "yes", "si", "1"):
            return True
        if lower in ("false", "off", "no", "0"):
            return False

        if raw.isdigit():
            try:
                return int(raw)
            except ValueError:
                pass

        try:
            return float(raw)
        except ValueError:
            pass

        return raw

    for key, raw_value in form.items():
        if not hasattr(config, key):
            continue
        value = parse_value(raw_value)
        setattr(config, key, value)

    db.commit()
    db.refresh(config)

    log_inbound_event(
        "config_saved",
        negocio_id=negocio_id,
        user_email=user["email"],
    )

    return RedirectResponse(
        url="/inbound/config",
        status_code=302,
    )


# ============================
#   ANALÍTICA / MÉTRICAS
# ============================

@router.get("/analytics", response_class=HTMLResponse)
async def inbound_analytics(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
):
    negocio_id = user["negocio_id"]
    negocio = _get_negocio(db, negocio_id)

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
    user=Depends(require_roles_dep("admin", "operador")),
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
    user=Depends(require_roles_dep("admin", "operador")),
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
):
    negocio_id = user["negocio_id"]
    negocio = _get_negocio(db, negocio_id)

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
    user=Depends(require_roles_dep("admin", "operador")),
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


# ============================
#   HEALTH DEL MÓDULO INBOUND
# ============================

@router.get("/health", response_class=JSONResponse)
async def inbound_health(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
):
    negocio_id = user["negocio_id"]

    try:
        total_recepciones = (
            db.query(InboundRecepcion)
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .count()
        )

        payload = {
            "status": "ok",
            "negocio_id": negocio_id,
            "total_recepciones": total_recepciones,
            "timestamp_utc": datetime.utcnow().isoformat(),
        }

        log_inbound_event(
            "health_ok",
            negocio_id=negocio_id,
            user_email=user["email"],
            total_recepciones=total_recepciones,
        )

        return payload

    except Exception as e:
        log_inbound_error(
            "health_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            error=str(e),
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "negocio_id": negocio_id,
                "error": str(e),
                "timestamp_utc": datetime.utcnow().isoformat(),
            },
        )


# ============================
#   DETALLE / ESTADOS
# ============================

@router.get("/{recepcion_id}", response_class=HTMLResponse)
async def inbound_detalle(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
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
            "detalle_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre)
        .all()
    )

    metrics = calcular_metricas_recepcion(recepcion)

    log_inbound_event(
        "detalle_recepcion_view",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        estado=recepcion.estado,
    )

    return templates.TemplateResponse(
        "inbound_detalle.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "productos": productos,
            "metrics": metrics,
            "modulo_nombre": "Orbion Inbound",
        },
    )


def _aplicar_cambio_estado(recepcion: InboundRecepcion, accion: str) -> None:
    ahora = datetime.utcnow()

    if accion == "marcar_en_espera":
        recepcion.estado = "EN_ESPERA"
        if recepcion.fecha_arribo is None:
            recepcion.fecha_arribo = ahora

    elif accion == "iniciar_descarga":
        recepcion.estado = "EN_DESCARGA"
        if recepcion.fecha_arribo is None:
            recepcion.fecha_arribo = ahora
        if recepcion.fecha_inicio_descarga is None:
            recepcion.fecha_inicio_descarga = ahora

    elif accion == "finalizar_descarga":
        recepcion.estado = "EN_CONTROL_CALIDAD"
        if recepcion.fecha_fin_descarga is None:
            recepcion.fecha_fin_descarga = ahora

    elif accion == "cerrar_recepcion":
        recepcion.estado = "CERRADO"

    else:
        raise HTTPException(status_code=400, detail="Acción de estado no soportada.")


@router.post("/{recepcion_id}/estado", response_class=HTMLResponse)
async def inbound_cambiar_estado(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    accion: str = Form(...),
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
            "cambio_estado_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            accion=accion,
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    estado_anterior = recepcion.estado

    _aplicar_cambio_estado(recepcion, accion)

    db.commit()
    db.refresh(recepcion)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_CAMBIO_ESTADO",
        detalle={
            "inbound_id": recepcion.id,
            "codigo": recepcion.codigo,
            "estado_anterior": estado_anterior,
            "estado_nuevo": recepcion.estado,
            "accion": accion,
        },
    )

    log_inbound_event(
        "cambio_estado",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        estado_anterior=estado_anterior,
        estado_nuevo=recepcion.estado,
        accion=accion,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )


# ============================
#   LÍNEAS
# ============================

@router.post("/{recepcion_id}/lineas", response_class=HTMLResponse)
async def inbound_agregar_linea(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    producto_id: int = Form(0),
    lote: str = Form(""),
    fecha_vencimiento: str = Form(""),
    cantidad_esperada: float = Form(0),
    cantidad_recibida: float = Form(0),
    unidad: str = Form(""),
    temperatura_objetivo: float = Form(None),
    temperatura_recibida: float = Form(None),
    observaciones: str = Form(""),

    # 👇 NUEVOS CAMPOS PARA PRODUCTO RÁPIDO
    nuevo_producto_nombre: str = Form(""),
    nuevo_producto_unidad_base: str = Form(""),
):
    negocio_id = user["negocio_id"]

    # ============================
    #   RESOLVER PRODUCTO
    # ============================
    producto_obj = None

    # 1) Si viene producto_id válido (>0), intentamos usarlo
    if producto_id:
        producto_obj = (
            db.query(Producto)
            .filter(
                Producto.id == producto_id,
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .first()
        )
        if not producto_obj:
            log_inbound_error(
                "agregar_linea_producto_invalido",
                negocio_id=negocio_id,
                recepcion_id=recepcion_id,
                user_email=user["email"],
                producto_id=producto_id,
            )
            raise HTTPException(
                status_code=400,
                detail="El producto seleccionado no es válido para este negocio.",
            )

    # 2) Si NO hay producto_id pero viene nombre de producto rápido -> creamos uno
    nuevo_nombre = (nuevo_producto_nombre or "").strip()
    nuevo_unidad = (nuevo_producto_unidad_base or "").strip()

    if not producto_obj and nuevo_nombre:
        producto_obj = Producto(
            negocio_id=negocio_id,
            nombre=nuevo_nombre,
            # 👇 usamos el campo real del modelo
            unidad=nuevo_unidad or "unidad",
            activo=1,
            origen="inbound",  # opcional pero útil para diferenciar
        )
        db.add(producto_obj)
        db.flush()  # para obtener producto_obj.id

        log_inbound_event(
            "producto_rapido_creado",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            producto_id=producto_obj.id,
            nombre=nuevo_nombre,
        )


    # 3) Si no hay ni producto_id ni nombre rápido -> error
    if not producto_obj:
        log_inbound_error(
            "agregar_linea_sin_producto",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
        )
        raise HTTPException(
            status_code=400,
            detail="Debes seleccionar un producto del catálogo o indicar un nombre de producto rápido.",
        )

    # ============================
    #   PARSEO FECHA VENCIMIENTO
    # ============================
    fecha_ven_dt = None
    if fecha_vencimiento:
        try:
            fecha_ven_dt = datetime.fromisoformat(fecha_vencimiento)
        except ValueError as e:
            log_inbound_error(
                "agregar_linea_fecha_venc_invalida",
                negocio_id=negocio_id,
                recepcion_id=recepcion_id,
                user_email=user["email"],
                raw_value=fecha_vencimiento,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail="Fecha de vencimiento inválida.")

    # ============================
    #   CREAR LÍNEA DE RECEPCIÓN
    # ============================
    try:
        linea = crear_linea_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            producto_id=producto_obj.id,
            lote=lote or None,
            fecha_vencimiento=fecha_ven_dt,
            cantidad_esperada=cantidad_esperada or None,
            cantidad_recibida=cantidad_recibida or None,
            unidad=unidad or None,
            temperatura_objetivo=temperatura_objetivo,
            temperatura_recibida=temperatura_recibida,
            observaciones=observaciones or None,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "agregar_linea_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_AGREGAR_LINEA",
        detalle={
            "inbound_id": recepcion_id,
            "linea_id": linea.id,
            "producto_id": producto_obj.id,
        },
    )

    log_inbound_event(
        "agregar_linea",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        linea_id=linea.id,
        producto_id=producto_obj.id,
    )

    db.commit()

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )

@router.get("/{recepcion_id}/lineas/nueva", response_class=HTMLResponse)
async def inbound_nueva_linea_form(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
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
            "nueva_linea_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre)
        .all()
    )

    log_inbound_event(
        "nueva_linea_form_view",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
    )

    return templates.TemplateResponse(
        "inbound_linea_form.html",
        {
            "request": request,
            "user": user,
            "recepcion": recepcion,
            "productos": productos,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/{recepcion_id}/producto-rapido", response_class=HTMLResponse)
async def inbound_producto_rapido(
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    nombre: str = Form(...),
    unidad: str = Form(""),
):
    negocio_id = user["negocio_id"]

    # Validamos que la recepción exista y pertenezca al negocio
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
            "producto_rapido_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    nombre_clean = (nombre or "").strip()
    if not nombre_clean:
        raise HTTPException(status_code=400, detail="El nombre del producto es obligatorio.")

    # Crear producto básico para este negocio
    producto = Producto(
        negocio_id=negocio_id,
        nombre=nombre_clean,
        unidad=(unidad or None),
        activo=1,
        # Si tu modelo tiene más campos, puedes setear defaults aquí
        # origen="INBOUND_RAPIDO",
    )

    db.add(producto)
    db.commit()
    db.refresh(producto)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_CREAR_PRODUCTO_RAPIDO",
        detalle={
            "inbound_id": recepcion_id,
            "producto_id": producto.id,
            "nombre": producto.nombre,
        },
    )

    log_inbound_event(
        "producto_rapido_creado",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        producto_id=producto.id,
        nombre=producto.nombre,
    )

    # Volvemos al detalle; el producto ya aparecerá en el select
    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )


@router.post("/{recepcion_id}/lineas/{linea_id}/eliminar", response_class=HTMLResponse)
async def inbound_eliminar_linea(
    recepcion_id: int,
    linea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
):
    negocio_id = user["negocio_id"]

    try:
        eliminar_linea_inbound(
            db=db,
            negocio_id=negocio_id,
            linea_id=linea_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "eliminar_linea_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            linea_id=linea_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_ELIMINAR_LINEA",
        detalle={
            "inbound_id": recepcion_id,
            "linea_id": linea_id,
        },
    )

    log_inbound_event(
        "eliminar_linea",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        linea_id=linea_id,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )


# ============================
#   INCIDENCIAS
# ============================

@router.post("/{recepcion_id}/incidencias", response_class=HTMLResponse)
async def inbound_agregar_incidencia(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    tipo: str = Form(...),
    criticidad: str = Form("media"),
    descripcion: str = Form(...),
):
    negocio_id = user["negocio_id"]

    try:
        incidencia = crear_incidencia_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            criticidad=criticidad,
            descripcion=descripcion,
        )
        incidencia.creado_por_id = user["id"]
        db.commit()
    except InboundDomainError as e:
        log_inbound_error(
            "agregar_incidencia_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            tipo=tipo,
            criticidad=criticidad,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_AGREGAR_INCIDENCIA",
        detalle={
            "inbound_id": recepcion_id,
            "incidencia_id": incidencia.id,
            "tipo": tipo,
            "criticidad": criticidad,
        },
    )

    log_inbound_event(
        "agregar_incidencia",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        incidencia_id=incidencia.id,
        tipo=tipo,
        criticidad=criticidad,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )


@router.post("/{recepcion_id}/incidencias/{incidencia_id}/eliminar", response_class=HTMLResponse)
async def inbound_eliminar_incidencia(
    recepcion_id: int,
    incidencia_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
):
    negocio_id = user["negocio_id"]

    try:
        eliminar_incidencia_inbound(
            db=db,
            negocio_id=negocio_id,
            incidencia_id=incidencia_id,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "eliminar_incidencia_domain_error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
            incidencia_id=incidencia_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_ELIMINAR_INCIDENCIA",
        detalle={
            "inbound_id": recepcion_id,
            "incidencia_id": incidencia_id,
        },
    )

    log_inbound_event(
        "eliminar_incidencia",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user["email"],
        incidencia_id=incidencia_id,
    )

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )
