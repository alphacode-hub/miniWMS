# modules/inbound_orbion/routes/routes_inbound_proveedores.py #
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

from modules.inbound_orbion.services.services_inbound_proveedores import (
    listar_proveedores,
    crear_proveedor,
    actualizar_proveedor,
    cambiar_estado_proveedor,
    crear_plantilla_proveedor,
    actualizar_plantilla_proveedor,
    cambiar_estado_plantilla_proveedor,
    eliminar_plantilla_proveedor,
    agregar_lineas_a_plantilla_proveedor,
    reemplazar_lineas_plantilla_proveedor,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ==========================================================
#   PROVEEDORES
# ==========================================================

@router.get("/proveedores", response_class=HTMLResponse)
async def inbound_proveedores_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    proveedores = listar_proveedores(
        db=db,
        negocio_id=negocio_id,
        solo_activos=False,
    )

    log_inbound_event(
        "proveedores_lista_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        total=len(proveedores),
    )

    return templates.TemplateResponse(
        "inbound_proveedores.html",
        {
            "request": request,
            "user": user,
            "proveedores": proveedores,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/proveedores/nuevo", response_class=HTMLResponse)
async def inbound_proveedor_crear(
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    nombre: str = Form(...),
    rut: str = Form(""),
    contacto: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = user["negocio_id"]

    try:
        proveedor = crear_proveedor(
            db=db,
            negocio_id=negocio_id,
            nombre=nombre,
            rut=rut,
            contacto=contacto,
            telefono=telefono,
            email=email,
            direccion=direccion,
            observaciones=observaciones,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "proveedor_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "proveedor_creado",
        negocio_id=negocio_id,
        user_email=user["email"],
        proveedor_id=proveedor.id,
    )

    return RedirectResponse("/inbound/proveedores", status_code=302)


@router.post("/proveedores/{proveedor_id}/estado", response_class=HTMLResponse)
async def inbound_proveedor_toggle(
    proveedor_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    activo: str = Form(...),
):
    negocio_id = user["negocio_id"]
    flag = activo.lower() in ("true", "on", "1", "si", "sí")

    try:
        proveedor = cambiar_estado_proveedor(
            db=db,
            negocio_id=negocio_id,
            proveedor_id=proveedor_id,
            activo=flag,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "proveedor_toggle_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            proveedor_id=proveedor_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "proveedor_estado_cambiado",
        negocio_id=negocio_id,
        user_email=user["email"],
        proveedor_id=proveedor.id,
        activo=proveedor.activo,
    )

    return RedirectResponse("/inbound/proveedores", status_code=302)


# ==========================================================
#   PLANTILLAS DE PROVEEDOR
# ==========================================================

@router.get("/proveedores/plantillas", response_class=HTMLResponse)
async def inbound_plantillas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    # Las plantillas se listan vía join en template
    from core.models import InboundPlantillaProveedor

    plantillas = (
        db.query(InboundPlantillaProveedor)
        .filter(InboundPlantillaProveedor.negocio_id == negocio_id)
        .order_by(InboundPlantillaProveedor.nombre.asc())
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


@router.post("/proveedores/{proveedor_id}/plantillas/nueva", response_class=HTMLResponse)
async def inbound_plantilla_crear(
    proveedor_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    nombre: str = Form(...),
    descripcion: str = Form(""),
):
    negocio_id = user["negocio_id"]

    try:
        plantilla = crear_plantilla_proveedor(
            db=db,
            negocio_id=negocio_id,
            proveedor_id=proveedor_id,
            nombre=nombre,
            descripcion=descripcion,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_crear_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            proveedor_id=proveedor_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "plantilla_creada",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla.id,
    )

    return RedirectResponse("/inbound/proveedores/plantillas", status_code=302)


@router.post("/proveedores/plantillas/{plantilla_id}/estado", response_class=HTMLResponse)
async def inbound_plantilla_toggle(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    activo: str = Form(...),
):
    negocio_id = user["negocio_id"]
    flag = activo.lower() in ("true", "on", "1", "si", "sí")

    try:
        plantilla = cambiar_estado_plantilla_proveedor(
            db=db,
            negocio_id=negocio_id,
            plantilla_id=plantilla_id,
            activo=flag,
        )
    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_toggle_domain_error",
            negocio_id=negocio_id,
            user_email=user["email"],
            plantilla_id=plantilla_id,
            error=e.message,
        )
        raise HTTPException(status_code=400, detail=e.message)

    log_inbound_event(
        "plantilla_estado_cambiado",
        negocio_id=negocio_id,
        user_email=user["email"],
        plantilla_id=plantilla.id,
        activo=plantilla.activo,
    )

    return RedirectResponse("/inbound/proveedores/plantillas", status_code=302)
