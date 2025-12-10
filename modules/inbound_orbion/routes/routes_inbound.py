# modules/inbound_orbion/routes/routes_inbound.py

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import HTTPException, APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, Producto
from core.security import require_roles_dep
from core.services.services_audit import registrar_auditoria

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

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(
    prefix="/inbound",
    tags=["inbound"],
)


# ============================
#   HELPERS
# ============================

def _generar_codigo_inbound(db: Session, negocio_id: int) -> str:
    # Muy simple, luego podemos mejorarlo
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
    user=Depends(require_roles_dep("admin", "operador")),
):
    negocio_id = user["negocio_id"]

    recepciones = (
        db.query(InboundRecepcion)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .order_by(InboundRecepcion.creado_en.desc())
        .all()
    )

    return templates.TemplateResponse(
        "inbound_lista.html",
        {
            "request": request,
            "user": user,
            "recepciones": recepciones,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def inbound_nuevo_form(
    request: Request,
    user=Depends(require_roles_dep("admin", "operador")),
):
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

    codigo = _generar_codigo_inbound(db, negocio_id)

    fecha_eta = None
    if fecha_estimada_llegada:
        # Asumimos formato YYYY-MM-DD
        fecha_eta = datetime.fromisoformat(fecha_estimada_llegada)

    recepcion = InboundRecepcion(
        negocio_id=negocio_id,
        codigo=codigo,
        proveedor=proveedor,
        referencia_externa=referencia_externa or None,
        contenedor=contenedor or None,
        patente_camion=patente_camion or None,
        tipo_carga=tipo_carga or None,
        fecha_estimada_llegada=fecha_eta,
        observaciones=observaciones or None,
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

    return RedirectResponse(
        url="/inbound",
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

    # Rango por defecto: últimos 30 días
    ahora = datetime.utcnow()
    hace_30 = ahora - timedelta(days=30)

    resumen = calcular_metricas_negocio(
        db=db,
        negocio_id=negocio_id,
        desde=hace_30,
        hasta=None,
    )

    # Traer recepciones recientes (por ejemplo, últimas 50)
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

    # Métricas por recepción
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

    # Calcular promedios por proveedor
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

    # Ordenar proveedores por promedio de tiempo (los más lentos arriba)
    proveedores_resumen.sort(
        key=lambda x: (x["promedio_tiempo_total_min"] is None, x["promedio_tiempo_total_min"] or 0),
        reverse=True,
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
        },
    )


@router.get("/metrics/resumen", response_class=JSONResponse)
async def inbound_metrics_resumen(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("admin", "operador")),
    desde: Optional[str] = None,  # formato YYYY-MM-DD
    hasta: Optional[str] = None,  # formato YYYY-MM-DD
):
    """
    Métricas agregadas del inbound del negocio:
    - total de recepciones
    - promedio de tiempos (espera, descarga, total) en minutos
    """
    negocio_id = user["negocio_id"]

    dt_desde = None
    dt_hasta = None

    if desde:
        try:
            dt_desde = datetime.fromisoformat(desde)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parámetro 'desde' inválido (usar YYYY-MM-DD).")

    if hasta:
        try:
            dt_hasta = datetime.fromisoformat(hasta)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parámetro 'hasta' inválido (usar YYYY-MM-DD).")

    metrics = calcular_metricas_negocio(
        db=db,
        negocio_id=negocio_id,
        desde=dt_desde,
        hasta=dt_hasta,
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
    """
    Devuelve un dataset crudo de recepciones inbound para analytics/ML.
    """
    negocio_id = user["negocio_id"]

    dt_desde = None
    dt_hasta = None

    if desde:
        try:
            dt_desde = datetime.fromisoformat(desde)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parámetro 'desde' inválido (usar YYYY-MM-DD).")

    if hasta:
        try:
            dt_hasta = datetime.fromisoformat(hasta)
        except ValueError:
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
    """
    Métricas de tiempos para una recepción específica.
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
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    metrics = calcular_metricas_recepcion(recepcion)

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
    """
    Aplica un cambio de estado de alto nivel sobre la recepción.
    """
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
    producto_id: int = Form(...),
    lote: str = Form(""),
    fecha_vencimiento: str = Form(""),
    cantidad_esperada: float = Form(0),
    cantidad_recibida: float = Form(0),
    unidad: str = Form(""),
    temperatura_objetivo: float = Form(None),
    temperatura_recibida: float = Form(None),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]

    # Parse fecha_vencimiento si viene
    fecha_ven_dt = None
    if fecha_vencimiento:
        try:
            fecha_ven_dt = datetime.fromisoformat(fecha_vencimiento)
        except ValueError:
            raise HTTPException(status_code=400, detail="Fecha de vencimiento inválida.")

    try:
        linea = crear_linea_inbound(
            db=db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            producto_id=producto_id,
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
        raise HTTPException(status_code=400, detail=e.message)

    registrar_auditoria(
        db=db,
        user=user,
        accion="INBOUND_AGREGAR_LINEA",
        detalle={
            "inbound_id": recepcion_id,
            "linea_id": linea.id,
            "producto_id": producto_id,
        },
    )

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

    return RedirectResponse(
        url=f"/inbound/{recepcion_id}",
        status_code=302,
    )
