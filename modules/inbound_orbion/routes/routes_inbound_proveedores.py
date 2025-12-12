# modules/inbound_orbion/routes/routes_inbound_proveedores.py

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import (
    InboundProveedorPlantilla,
    InboundProveedorProducto,
    Producto,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


@router.get("/proveedores/plantillas", response_class=HTMLResponse)
async def inbound_proveedores_plantillas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantillas = (
        db.query(InboundProveedorPlantilla)
        .filter(InboundProveedorPlantilla.negocio_id == negocio_id)
        .order_by(InboundProveedorPlantilla.nombre_proveedor.asc())
        .all()
    )

    return templates.TemplateResponse(
        "inbound_proveedores_plantillas.html",
        {
            "request": request,
            "user": user,
            "plantillas": plantillas,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/proveedores/plantillas/nueva", response_class=HTMLResponse)
async def inbound_proveedores_plantillas_nueva(
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    nombre_proveedor: str = Form(...),
    alias: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantilla = InboundProveedorPlantilla(
        negocio_id=negocio_id,
        nombre_proveedor=nombre_proveedor.strip(),
        alias=alias.strip() or None,
        observaciones=observaciones.strip() or None,
        activo=True,
    )

    db.add(plantilla)
    db.commit()

    log_inbound_event(
        "proveedor_plantilla_creada",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla.id,
    )

    return RedirectResponse(
        url="/inbound/proveedores/plantillas",
        status_code=302,
    )


@router.post("/proveedores/plantillas/{plantilla_id}/toggle", response_class=HTMLResponse)
async def inbound_proveedores_plantillas_toggle(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantilla = (
        db.query(InboundProveedorPlantilla)
        .filter(
            InboundProveedorPlantilla.id == plantilla_id,
            InboundProveedorPlantilla.negocio_id == negocio_id,
        )
        .first()
    )
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    plantilla.activo = not plantilla.activo
    db.commit()

    log_inbound_event(
        "proveedor_plantilla_toggle",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla.id,
        activo=plantilla.activo,
    )

    return RedirectResponse(
        url="/inbound/proveedores/plantillas",
        status_code=302,
    )


@router.get("/proveedores/plantillas/{plantilla_id}", response_class=HTMLResponse)
async def inbound_proveedores_plantilla_detalle(
    plantilla_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantilla = (
        db.query(InboundProveedorPlantilla)
        .filter(
            InboundProveedorPlantilla.id == plantilla_id,
            InboundProveedorPlantilla.negocio_id == negocio_id,
        )
        .first()
    )
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    productos_plantilla = (
        db.query(InboundProveedorProducto)
        .filter(InboundProveedorProducto.plantilla_id == plantilla_id)
        .all()
    )

    productos_catalogo = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )

    return templates.TemplateResponse(
        "inbound_proveedores_plantilla_detalle.html",
        {
            "request": request,
            "user": user,
            "plantilla": plantilla,
            "productos_plantilla": productos_plantilla,
            "productos_catalogo": productos_catalogo,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/proveedores/plantillas/{plantilla_id}/productos/nuevo", response_class=HTMLResponse)
async def inbound_proveedores_plantilla_producto_nuevo(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    producto_id: int = Form(0),
    nombre_producto: str = Form(""),
    unidad: str = Form(""),
    cantidad_por_defecto: float = Form(None),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantilla = (
        db.query(InboundProveedorPlantilla)
        .filter(
            InboundProveedorPlantilla.id == plantilla_id,
            InboundProveedorPlantilla.negocio_id == negocio_id,
        )
        .first()
    )
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    producto_fk = None
    nombre_final = None

    if producto_id:
        producto_fk = (
            db.query(Producto)
            .filter(
                Producto.id == producto_id,
                Producto.negocio_id == negocio_id,
            )
            .first()
        )
        if not producto_fk:
            raise HTTPException(status_code=400, detail="Producto inválido para la plantilla.")
        nombre_final = producto_fk.nombre
    else:
        nombre_final = nombre_producto.strip()

    if not nombre_final:
        raise HTTPException(status_code=400, detail="Debe indicar un producto del catálogo o un nombre de producto.")

    pp = InboundProveedorProducto(
        plantilla_id=plantilla_id,
        producto_id=producto_fk.id if producto_fk else None,
        nombre_producto=nombre_final,
        unidad=unidad.strip() or None,
        cantidad_por_defecto=cantidad_por_defecto,
    )

    db.add(pp)
    db.commit()

    log_inbound_event(
        "proveedor_plantilla_producto_agregado",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla_id,
    )

    return RedirectResponse(
        url=f"/inbound/proveedores/plantillas/{plantilla_id}",
        status_code=302,
    )


@router.post("/proveedores/plantillas/{plantilla_id}/productos/{pp_id}/eliminar", response_class=HTMLResponse)
async def inbound_proveedores_plantilla_producto_eliminar(
    plantilla_id: int,
    pp_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    pp = (
        db.query(InboundProveedorProducto)
        .join(InboundProveedorPlantilla, InboundProveedorPlantilla.id == InboundProveedorProducto.plantilla_id)
        .filter(
            InboundProveedorProducto.id == pp_id,
            InboundProveedorPlantilla.id == plantilla_id,
            InboundProveedorPlantilla.negocio_id == negocio_id,
        )
        .first()
    )
    if not pp:
        raise HTTPException(status_code=404, detail="Producto de plantilla no encontrado")

    db.delete(pp)
    db.commit()

    log_inbound_event(
        "proveedor_plantilla_producto_eliminado",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla_id,
        pp_id=pp_id,
    )

    return RedirectResponse(
        url=f"/inbound/proveedores/plantillas/{plantilla_id}",
        status_code=302,
    )
