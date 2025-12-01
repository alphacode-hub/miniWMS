# routes_products.py
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import Producto
from security import require_roles_dep
from services_plan_limits import check_plan_limit
from services_audit import registrar_auditoria


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER PRODUCTOS
# ============================

router = APIRouter(
    prefix="",
    tags=["productos"],
)


# ============================
#     PRODUCTOS
# ============================

@router.get("/productos", response_class=HTMLResponse)
async def productos_list(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Lista los productos.
    - admin: ve productos de su negocio
    - superadmin: ve todos los productos
    """
    if user["rol"] == "superadmin":
        productos = (
            db.query(Producto)
            .order_by(Producto.nombre.asc())
            .all()
        )
    else:
        negocio_id = user["negocio_id"]
        productos = (
            db.query(Producto)
            .filter(Producto.negocio_id == negocio_id)
            .order_by(Producto.nombre.asc())
            .all()
        )

    return templates.TemplateResponse(
        "productos.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
        },
    )


@router.get("/productos/nuevo", response_class=HTMLResponse)
async def producto_nuevo_form(
    request: Request,
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Formulario de creación de producto.
    Solo admin del negocio.
    """
    return templates.TemplateResponse(
        "producto_nuevo.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "nombre": "",
            "unidad": "unidad",
            "stock_min": "",
            "stock_max": "",
            "costo_unitario": "",
        },
    )


@router.post("/productos/nuevo", response_class=HTMLResponse)
async def producto_nuevo_submit(
    request: Request,
    nombre: str = Form(...),
    unidad: str = Form(...),
    stock_min: str = Form(""),
    stock_max: str = Form(""),
    costo_unitario: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Procesa la creación de un nuevo producto del negocio actual.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    nombre = (nombre or "").strip()
    unidad = (unidad or "").strip() or "unidad"

    stock_min_str = (stock_min or "").strip()
    stock_max_str = (stock_max or "").strip()

    stock_min_val = int(stock_min_str) if stock_min_str.isdigit() else None
    stock_max_val = int(stock_max_str) if stock_max_str.isdigit() else None

    costo_str = (costo_unitario or "").strip().replace(",", ".")
    try:
        costo_val = float(costo_str) if costo_str else None
    except ValueError:
        return templates.TemplateResponse(
            "producto_nuevo.html",
            {
                "request": request,
                "user": user,
                "error": "El costo unitario debe ser un número válido.",
                "nombre": nombre,
                "unidad": unidad,
                "stock_min": stock_min_str,
                "stock_max": stock_max_str,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    if not nombre:
        return templates.TemplateResponse(
            "producto_nuevo.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre del producto no puede estar vacío.",
                "nombre": nombre,
                "unidad": unidad,
                "stock_min": stock_min_str,
                "stock_max": stock_max_str,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Validar que no exista ya el mismo nombre para el mismo negocio
    existe = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            func.lower(Producto.nombre) == nombre.lower(),
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "producto_nuevo.html",
            {
                "request": request,
                "user": user,
                "error": f"Ya existe un producto con el nombre '{nombre}'.",
                "nombre": nombre,
                "unidad": unidad,
                "stock_min": stock_min_str,
                "stock_max": stock_max_str,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Aplicar límite de plan
    check_plan_limit(db, negocio_id, "productos")

    producto = Producto(
        negocio_id=negocio_id,
        nombre=nombre,
        unidad=unidad,
        stock_min=stock_min_val,
        stock_max=stock_max_val,
        activo=1,
        costo_unitario=costo_val,
    )
    db.add(producto)
    db.commit()
    db.refresh(producto)

    registrar_auditoria(
        db,
        user,
        accion="producto_creado",
        detalle={
            "producto_id": producto.id,
            "nombre": producto.nombre,
            "unidad": producto.unidad,
            "stock_min": producto.stock_min,
            "stock_max": producto.stock_max,
            "costo_unitario": producto.costo_unitario,
        },
    )

    print(
        ">>> NUEVO PRODUCTO:",
        producto.nombre,
        "min:",
        producto.stock_min,
        "max:",
        producto.stock_max,
    )

    return RedirectResponse(url="/productos", status_code=302)


@router.get("/productos/{producto_id}/editar", response_class=HTMLResponse)
async def producto_editar_form(
    producto_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Formulario de edición de producto.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
        )
        .first()
    )
    if not producto:
        return RedirectResponse(url="/productos", status_code=302)

    return templates.TemplateResponse(
        "producto_editar.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "producto": producto,
        },
    )


@router.post("/productos/{producto_id}/editar", response_class=HTMLResponse)
async def producto_editar_submit(
    producto_id: int,
    request: Request,
    nombre: str = Form(...),
    unidad: str = Form(...),
    stock_min: str = Form(""),
    stock_max: str = Form(""),
    costo_unitario: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Procesa la edición de un producto existente del negocio actual.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
        )
        .first()
    )
    if not producto:
        return RedirectResponse(url="/productos", status_code=302)

    nombre = (nombre or "").strip()
    unidad = (unidad or "").strip() or "unidad"
    stock_min_str = (stock_min or "").strip()
    stock_max_str = (stock_max or "").strip()

    stock_min_val = int(stock_min_str) if stock_min_str.isdigit() else None
    stock_max_val = int(stock_max_str) if stock_max_str.isdigit() else None

    costo_str = (costo_unitario or "").strip().replace(",", ".")
    try:
        costo_val = float(costo_str) if costo_str else None
    except ValueError:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": "El costo unitario debe ser un número válido.",
                "producto": producto,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    if not nombre:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre del producto no puede estar vacío.",
                "producto": producto,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Validar nombre único dentro del negocio (excluyendo el mismo producto)
    existe = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            func.lower(Producto.nombre) == nombre.lower(),
            Producto.id != producto.id,
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": f"Ya existe otro producto con el nombre '{nombre}'.",
                "producto": producto,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Guardar cambios
    producto.nombre = nombre
    producto.unidad = unidad
    producto.stock_min = stock_min_val
    producto.stock_max = stock_max_val
    producto.costo_unitario = costo_val

    db.commit()
    db.refresh(producto)

    registrar_auditoria(
        db,
        user,
        accion="producto_editado",
        detalle={
            "producto_id": producto.id,
            "nombre": producto.nombre,
            "unidad": producto.unidad,
            "stock_min": producto.stock_min,
            "stock_max": producto.stock_max,
            "costo_unitario": producto.costo_unitario,
        },
    )

    return RedirectResponse(url="/productos", status_code=302)


@router.post("/productos/{producto_id}/toggle")
async def producto_toggle(
    producto_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Activa / desactiva un producto.
    Solo admin del negocio puede realizar este cambio.
    """
    negocio_id = user["negocio_id"]

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
        )
        .first()
    )

    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    # Cambiar estado: 1 <-> 0
    producto.activo = 0 if producto.activo else 1
    db.commit()

    # Registrar auditoría
    registrar_auditoria(
        db,
        user,
        accion="producto_toggle",
        detalle={
            "producto_id": producto.id,
            "nuevo_estado": producto.activo,
        },
    )

    return RedirectResponse("/productos", status_code=302)
