# modules/inbound_orbion/routes/routes_inbound_core.py

from datetime import datetime
import math
from typing import Optional
from collections import defaultdict

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, Producto, InboundConfig
from core.services.services_audit import registrar_auditoria
from core.plans import get_inbound_plan_config

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    crear_linea_inbound,
    calcular_metricas_recepcion,
    calcular_metricas_negocio,
)
from modules.inbound_orbion.services.services_inbound_config import (
    check_inbound_recepcion_limit,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


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


# ============================
#   LISTA / NUEVA RECEPCIÓN
# ============================

@router.get("/", response_class=HTMLResponse)
async def inbound_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    estado: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    page: int = 1,
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)
    inbound_analytics_enabled = bool(plan_cfg.get("enable_inbound_analytics", False))

    q = db.query(InboundRecepcion).filter(
        InboundRecepcion.negocio_id == negocio_id,
    )

    if estado:
        q = q.filter(InboundRecepcion.estado == estado)

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
            dt_hasta = datetime.fromisoformat(hasta)
            dt_hasta = dt_hasta.replace(hour=23, minute=59, second=59)
            q = q.filter(InboundRecepcion.creado_en <= dt_hasta)
        except ValueError:
            dt_hasta = None

    per_page = 5
    total_recepciones = q.count()
    total_pages = max(1, math.ceil(total_recepciones / per_page)) if total_recepciones > 0 else 1

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

    qs_parts = []
    if estado:
        qs_parts.append(f"estado={estado}")
    if desde:
        qs_parts.append(f"desde={desde}")
    if hasta:
        qs_parts.append(f"hasta={hasta}")
    base_query = "&".join(qs_parts)

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
    user=Depends(inbound_roles_dep()),
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
    user=Depends(inbound_roles_dep()),
    proveedor: str = Form(...),
    referencia_externa: str = Form(""),
    contenedor: str = Form(""),
    patente_camion: str = Form(""),
    tipo_carga: str = Form(""),
    fecha_estimada_llegada: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    proveedor = proveedor.strip()
    referencia_externa = referencia_externa.strip() or None
    contenedor_norm = contenedor.strip().upper() or None
    patente_norm = patente_camion.strip().upper() or None
    tipo_carga = tipo_carga.strip() or None
    observaciones_norm = observaciones.strip() or None

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
#   DETALLE / ESTADOS
# ============================

@router.get("/{recepcion_id}", response_class=HTMLResponse)
async def inbound_detalle(
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
    user=Depends(inbound_roles_dep()),
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
#   HEALTH DEL MÓDULO INBOUND
# ============================

@router.get("/health", response_class=JSONResponse)
async def inbound_health(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
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
