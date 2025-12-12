# modules/inbound_orbion/routes/routes_inbound_proveedores.py

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import (
    Proveedor,
    InboundPlantillaProveedor,
    InboundPlantillaProveedorLinea,
    Producto,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ============================
#   LISTA DE PLANTILLAS
# ============================

@router.get("/proveedores/plantillas", response_class=HTMLResponse)
async def inbound_proveedores_plantillas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    # Traemos plantillas y proveedor asociado
    plantillas = (
        db.query(InboundPlantillaProveedor)
        .join(Proveedor, Proveedor.id == InboundPlantillaProveedor.proveedor_id)
        .filter(InboundPlantillaProveedor.negocio_id == negocio_id)
        .order_by(Proveedor.nombre.asc(), InboundPlantillaProveedor.nombre.asc())
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


# ============================
#   CREAR PLANTILLA
# ============================

@router.post("/proveedores/plantillas/nueva", response_class=HTMLResponse)
async def inbound_proveedores_plantillas_nueva(
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    proveedor_id: int = Form(...),
    nombre_plantilla: str = Form(...),
    descripcion: str = Form(""),
):
    """
    Crea una nueva plantilla ligada a un proveedor existente.
    """
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    proveedor = (
        db.query(Proveedor)
        .filter(
            Proveedor.id == proveedor_id,
            Proveedor.negocio_id == negocio_id,
        )
        .first()
    )
    if not proveedor:
        raise HTTPException(status_code=400, detail="Proveedor inválido para esta plantilla.")

    if not nombre_plantilla.strip():
        raise HTTPException(status_code=400, detail="El nombre de la plantilla es obligatorio.")

    plantilla = InboundPlantillaProveedor(
        negocio_id=negocio_id,
        proveedor_id=proveedor.id,
        nombre=nombre_plantilla.strip(),
        descripcion=descripcion.strip() or None,
        activo=True,
    )

    db.add(plantilla)
    db.commit()
    db.refresh(plantilla)

    log_inbound_event(
        "proveedor_plantilla_creada",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla.id,
        proveedor_id=proveedor.id,
    )

    return RedirectResponse(
        url="/inbound/proveedores/plantillas",
        status_code=302,
    )


# ============================
#   ACTIVAR / DESACTIVAR PLANTILLA
# ============================

@router.post("/proveedores/plantillas/{plantilla_id}/toggle", response_class=HTMLResponse)
async def inbound_proveedores_plantillas_toggle(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantilla = (
        db.query(InboundPlantillaProveedor)
        .filter(
            InboundPlantillaProveedor.id == plantilla_id,
            InboundPlantillaProveedor.negocio_id == negocio_id,
        )
        .first()
    )
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    plantilla.activo = not plantilla.activo
    db.commit()
    db.refresh(plantilla)

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


# ============================
#   DETALLE DE PLANTILLA
# ============================

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
        db.query(InboundPlantillaProveedor)
        .filter(
            InboundPlantillaProveedor.id == plantilla_id,
            InboundPlantillaProveedor.negocio_id == negocio_id,
        )
        .first()
    )
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    productos_plantilla = (
        db.query(InboundPlantillaProveedorLinea)
        .filter(InboundPlantillaProveedorLinea.plantilla_id == plantilla_id)
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


# ============================
#   AGREGAR PRODUCTO A PLANTILLA
# ============================

@router.post("/proveedores/plantillas/{plantilla_id}/productos/nuevo", response_class=HTMLResponse)
async def inbound_proveedores_plantilla_producto_nuevo(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    producto_id: int = Form(...),
    unidad: str = Form(""),
    cantidad_por_defecto: float = Form(None),
    peso_kg_sugerido: float = Form(None),
):
    """
    Agrega una línea a la plantilla. Ahora la línea está SIEMPRE ligada
    a un Producto del catálogo (no se guarda texto libre de nombre).
    """
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    plantilla = (
        db.query(InboundPlantillaProveedor)
        .filter(
            InboundPlantillaProveedor.id == plantilla_id,
            InboundPlantillaProveedor.negocio_id == negocio_id,
        )
        .first()
    )
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .first()
    )
    if not producto:
        raise HTTPException(status_code=400, detail="Producto inválido para la plantilla.")

    linea = InboundPlantillaProveedorLinea(
        plantilla_id=plantilla_id,
        producto_id=producto.id,
        cantidad_sugerida=cantidad_por_defecto,
        unidad=unidad.strip() or None,
        peso_kg_sugerido=peso_kg_sugerido,
    )

    db.add(linea)
    db.commit()
    db.refresh(linea)

    log_inbound_event(
        "proveedor_plantilla_producto_agregado",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla_id,
        producto_id=producto.id,
    )

    return RedirectResponse(
        url=f"/inbound/proveedores/plantillas/{plantilla_id}",
        status_code=302,
    )


# ============================
#   ELIMINAR PRODUCTO DE PLANTILLA
# ============================

@router.post("/proveedores/plantillas/{plantilla_id}/productos/{linea_id}/eliminar", response_class=HTMLResponse)
async def inbound_proveedores_plantilla_producto_eliminar(
    plantilla_id: int,
    linea_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    linea = (
        db.query(InboundPlantillaProveedorLinea)
        .join(
            InboundPlantillaProveedor,
            InboundPlantillaProveedor.id == InboundPlantillaProveedorLinea.plantilla_id,
        )
        .filter(
            InboundPlantillaProveedorLinea.id == linea_id,
            InboundPlantillaProveedor.id == plantilla_id,
            InboundPlantillaProveedor.negocio_id == negocio_id,
        )
        .first()
    )
    if not linea:
        raise HTTPException(status_code=404, detail="Producto de plantilla no encontrado")

    db.delete(linea)
    db.commit()

    log_inbound_event(
        "proveedor_plantilla_producto_eliminado",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla_id,
        linea_id=linea_id,
    )

    return RedirectResponse(
        url=f"/inbound/proveedores/plantillas/{plantilla_id}",
        status_code=302,
    )
