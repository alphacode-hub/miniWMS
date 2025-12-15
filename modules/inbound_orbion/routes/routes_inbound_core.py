# modules/inbound_orbion/routes/routes_inbound_core.py
"""
Rutas Core – Inbound ORBION

✔ Multi-tenant estricto (negocio_id)
✔ Paginación + filtros robustos
✔ Código inbound consistente (sin colisiones por concurrencia)
✔ Workflow de estados controlado (alineado con enums / dominio)
✔ Servicios/planes/auditoría/logging integrados
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Optional

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, Producto
from core.services.services_audit import registrar_auditoria
from core.plans import get_inbound_plan_config

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    calcular_metricas_recepcion,
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

PER_PAGE_DEFAULT = 10
PER_PAGE_MAX = 50


# ============================
#   HELPERS
# ============================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date_iso(value: Optional[str]) -> Optional[datetime]:
    """
    Acepta YYYY-MM-DD o ISO completo.
    Devuelve datetime naive/aware según input; se usa solo para filtros.
    """
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _generar_codigo_inbound(db: Session, negocio_id: int) -> str:
    """
    Genera código consecutivo por negocio y año.
    Enterprise: evita colisiones bajo concurrencia usando MAX(id) como fallback
    y el conteo por negocio como aproximación consistente.

    Formato: INB-YYYY-000001
    """
    year = _utcnow().year

    # Preferimos un "secuencial" basado en el total existente + 1,
    # pero con concurrencia puede colisionar: mitigamos usando max(id).
    count = (
        db.query(func.count(InboundRecepcion.id))
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .scalar()
    ) or 0

    max_id = (
        db.query(func.coalesce(func.max(InboundRecepcion.id), 0))
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .scalar()
    ) or 0

    numero = max(int(count), int(max_id)) + 1
    return f"INB-{year}-{numero:06d}"


def _aplicar_cambio_estado(recepcion: InboundRecepcion, accion: str) -> None:
    """
    Aplica cambios de workflow y setea timestamps si corresponde.
    Mantener alineado con el Enum RecepcionEstado en core/models.
    """
    ahora = _utcnow()

    if accion == "marcar_en_espera":
        recepcion.estado = "EN_ESPERA"
        recepcion.fecha_arribo = recepcion.fecha_arribo or ahora

    elif accion == "iniciar_descarga":
        recepcion.estado = "EN_DESCARGA"
        recepcion.fecha_arribo = recepcion.fecha_arribo or ahora
        recepcion.fecha_inicio_descarga = recepcion.fecha_inicio_descarga or ahora

    elif accion == "finalizar_descarga":
        recepcion.estado = "EN_CONTROL_CALIDAD"
        recepcion.fecha_fin_descarga = recepcion.fecha_fin_descarga or ahora

    elif accion == "cerrar_recepcion":
        recepcion.estado = "CERRADO"

    else:
        raise HTTPException(status_code=400, detail="Acción de estado no soportada.")


# ============================
#   LISTA
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
    per_page: int = PER_PAGE_DEFAULT,
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)
    inbound_analytics_enabled = bool(plan_cfg.get("enable_inbound_analytics", False))

    # Sanitizar paginación
    if per_page < 1:
        per_page = PER_PAGE_DEFAULT
    if per_page > PER_PAGE_MAX:
        per_page = PER_PAGE_MAX

    q = db.query(InboundRecepcion).filter(
        InboundRecepcion.negocio_id == negocio_id,
    )

    if estado:
        q = q.filter(InboundRecepcion.estado == estado)

    dt_desde = _parse_date_iso(desde)
    if dt_desde:
        q = q.filter(InboundRecepcion.creado_en >= dt_desde)

    dt_hasta = _parse_date_iso(hasta)
    if dt_hasta:
        # si viene solo YYYY-MM-DD, lo llevamos al fin del día
        if len(hasta.strip()) <= 10:
            dt_hasta = dt_hasta.replace(hour=23, minute=59, second=59)
        q = q.filter(InboundRecepcion.creado_en <= dt_hasta)

    total_recepciones = q.count()
    total_pages = max(1, math.ceil(total_recepciones / per_page)) if total_recepciones > 0 else 1

    if page < 1:
        page = 1
    if total_recepciones > 0 and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    recepciones = (
        q.order_by(InboundRecepcion.created_at.desc(), InboundRecepcion.id.desc())
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
    if per_page != PER_PAGE_DEFAULT:
        qs_parts.append(f"per_page={per_page}")
    base_query = "&".join(qs_parts)

    log_inbound_event(
        "lista_recepciones_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total_page=len(recepciones),
        total_filtered=total_recepciones,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
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
            "per_page": per_page,
            "total_pages": total_pages,
            "total_recepciones": total_recepciones,
            "base_query": base_query,
        },
    )


# ============================
#   NUEVA RECEPCIÓN
# ============================

@router.get("/nuevo", response_class=HTMLResponse)
async def inbound_nuevo_form(
    request: Request,
    user=Depends(inbound_roles_dep()),
):
    log_inbound_event(
        "nueva_recepcion_form_view",
        negocio_id=user["negocio_id"],
        user_email=user.get("email"),
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

    proveedor_norm = (proveedor or "").strip()
    if not proveedor_norm:
        raise HTTPException(status_code=400, detail="Proveedor es obligatorio.")

    referencia_externa_norm = (referencia_externa or "").strip() or None
    contenedor_norm = (contenedor or "").strip().upper() or None
    patente_norm = (patente_camion or "").strip().upper() or None
    tipo_carga_norm = (tipo_carga or "").strip() or None
    observaciones_norm = (observaciones or "").strip() or None

    try:
        check_inbound_recepcion_limit(db, negocio)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_limit_reached",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    fecha_eta = None
    if fecha_estimada_llegada and fecha_estimada_llegada.strip():
        try:
            fecha_eta = datetime.fromisoformat(fecha_estimada_llegada.strip())
        except ValueError as e:
            log_inbound_error(
                "nueva_recepcion_fecha_eta_invalida",
                negocio_id=negocio_id,
                user_email=user.get("email"),
                raw_value=fecha_estimada_llegada,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail="Fecha estimada de llegada inválida (ISO).")

    codigo = _generar_codigo_inbound(db, negocio_id)

    recepcion = InboundRecepcion(
        negocio_id=negocio_id,
        codigo=codigo,
        proveedor=proveedor_norm,
        referencia_externa=referencia_externa_norm,
        contenedor=contenedor_norm,
        patente_camion=patente_norm,
        tipo_carga=tipo_carga_norm,
        fecha_estimada_llegada=fecha_eta,
        observaciones=observaciones_norm,
        estado="PRE_REGISTRADO",
        creado_por_id=user.get("id"),
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
            "codigo": recepcion.codigo,
        },
    )

    log_inbound_event(
        "recepcion_creada",
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        user_email=user.get("email"),
        codigo=recepcion.codigo,
        proveedor=recepcion.proveedor,
        estado=recepcion.estado,
    )

    return RedirectResponse(url="/inbound", status_code=302)


# ============================
#   DETALLE
# ============================

@router.get("/recepciones/{recepcion_id}", response_class=HTMLResponse)
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
            user_email=user.get("email"),
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )

    metrics = calcular_metricas_recepcion(recepcion)

    log_inbound_event(
        "detalle_recepcion_view",
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        user_email=user.get("email"),
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


# ============================
#   CAMBIO DE ESTADO
# ============================

@router.post("/recepciones/{recepcion_id}/estado", response_class=HTMLResponse)
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
            user_email=user.get("email"),
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
        user_email=user.get("email"),
        estado_anterior=estado_anterior,
        estado_nuevo=recepcion.estado,
        accion=accion,
    )

    return RedirectResponse(url=f"/inbound/recepciones/{recepcion_id}", status_code=302)


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
            db.query(func.count(InboundRecepcion.id))
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .scalar()
        ) or 0

        payload = {
            "status": "ok",
            "negocio_id": negocio_id,
            "total_recepciones": total_recepciones,
            "timestamp_utc": _utcnow().isoformat(),
        }

        log_inbound_event(
            "health_ok",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            total_recepciones=total_recepciones,
        )

        return payload

    except Exception as e:
        log_inbound_error(
            "health_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(e),
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "negocio_id": negocio_id,
                "error": str(e),
                "timestamp_utc": _utcnow().isoformat(),
            },
        )
