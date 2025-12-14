# modules/inbound_orbion/routes/routes_inbound_lineas.py

from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundRecepcion, Producto
from core.services.services_audit import registrar_auditoria

from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    crear_linea_inbound,
    eliminar_linea_inbound,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================
#   LÍNEAS
# ============================

@router.post("/recepciones/{recepcion_id}/lineas", response_class=HTMLResponse)
async def inbound_agregar_linea(
    recepcion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    producto_id: int = Form(0),
    lote: str = Form(""),
    fecha_vencimiento: str = Form(""),
    cantidad_esperada: float = Form(0),
    cantidad_recibida: float = Form(0),
    unidad: str = Form(""),
    temperatura_objetivo: Optional[float] = Form(None),
    temperatura_recibida: Optional[float] = Form(None),
    observaciones: str = Form(""),
    bultos: Optional[int] = Form(None),
    kilos: Optional[float] = Form(None),

    # Producto rápido
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
            unidad = (nuevo_unidad or "").strip() or "unidad",
            activo=1,
            origen="inbound",
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
            peso_kg=kilos,     # ✅ UI kilos -> modelo peso_kg
            bultos=bultos,
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

    

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}",
        status_code=302,
    )


@router.get("/recepciones/{recepcion_id}/lineas/nueva", response_class=HTMLResponse)
async def inbound_nueva_linea_form(
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


@router.post("/recepciones/{recepcion_id}/producto-rapido", response_class=HTMLResponse)
async def inbound_producto_rapido(
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    nombre: str = Form(...),
    unidad: str = Form(""),
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
            "producto_rapido_recepcion_not_found",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            user_email=user["email"],
        )
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    nombre_clean = (nombre or "").strip()
    if not nombre_clean:
        raise HTTPException(status_code=400, detail="El nombre del producto es obligatorio.")

    producto = Producto(
        negocio_id=negocio_id,
        nombre=nombre_clean,
        unidad=(unidad or None),
        activo=1,
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

    return RedirectResponse(
        url=f"/inbound/recepciones/{recepcion_id}",
        status_code=302,
    )


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
        url=f"/inbound/recepciones/{recepcion_id}",
        status_code=302,
    )
