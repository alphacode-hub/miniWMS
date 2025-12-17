from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models.inbound.proveedores import Proveedor

from modules.inbound_orbion.services.services_inbound_proveedores import (
    listar_proveedores,
    crear_proveedor,
    actualizar_proveedor,
    cambiar_estado_proveedor,
    crear_plantilla_proveedor,
    actualizar_plantilla_proveedor,
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

from core.models.inbound.plantillas_checklist import InboundPlantillaChecklist

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ==========================================================
# Helpers UX
# ==========================================================

def _qp(msg: str | None) -> str:
    return quote_plus((msg or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


# ==========================================================
# PROVEEDORES
# ==========================================================

@router.get("/proveedores", response_class=HTMLResponse)
async def inbound_proveedores_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    proveedores = listar_proveedores(
        db=db,
        negocio_id=negocio_id,
        solo_activos=False,
    )

    log_inbound_event(
        "proveedores_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total=len(proveedores),
    )

    return templates.TemplateResponse(
        "inbound_proveedores.html",
        {
            "request": request,
            "user": user,
            "proveedores": proveedores,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/proveedores/nuevo", response_class=HTMLResponse)
async def inbound_proveedor_crear(
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    nombre: str = Form(...),
    rut: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
):
    negocio_id = int(user["negocio_id"])

    try:
        proveedor = crear_proveedor(
            db=db,
            negocio_id=negocio_id,
            nombre=nombre,
            rut=rut,
            telefono=telefono,
            email=email,
        )

        log_inbound_event(
            "proveedor_creado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor.id,
        )

        return _redirect("/inbound/proveedores", ok="Proveedor creado correctamente.")

    except InboundDomainError as e:
        log_inbound_error(
            "proveedor_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(e),
        )
        return _redirect("/inbound/proveedores", error=str(e))


@router.post("/proveedores/{proveedor_id}/estado", response_class=HTMLResponse)
async def inbound_proveedor_toggle(
    proveedor_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    activo: str = Form(...),
):
    negocio_id = int(user["negocio_id"])
    flag = activo.lower() in ("true", "on", "1", "si", "sí")

    try:
        proveedor = cambiar_estado_proveedor(
            db=db,
            negocio_id=negocio_id,
            proveedor_id=proveedor_id,
            activo=flag,
        )

        log_inbound_event(
            "proveedor_estado_cambiado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor.id,
            activo=proveedor.activo,
        )

        return _redirect("/inbound/proveedores", ok="Estado del proveedor actualizado.")

    except InboundDomainError as e:
        log_inbound_error(
            "proveedor_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor_id,
            error=str(e),
        )
        return _redirect("/inbound/proveedores", error=str(e))


# ==========================================================
# PLANTILLAS DE CHECKLIST POR PROVEEDOR
# ==========================================================

@router.get("/proveedores/plantillas", response_class=HTMLResponse)
async def inbound_plantillas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    

    plantillas = (
        db.query(InboundPlantillaChecklist)
        .filter(InboundPlantillaChecklist.negocio_id == negocio_id)
        .order_by(InboundPlantillaChecklist.nombre.asc())
        .all()
    )

    return templates.TemplateResponse(
        "inbound_proveedores_plantillas.html",
        {
            "request": request,
            "user": user,
            "plantillas": plantillas,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
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
    negocio_id = int(user["negocio_id"])

    try:
        plantilla = crear_plantilla_proveedor(
            db=db,
            negocio_id=negocio_id,
            proveedor_id=proveedor_id,
            nombre=nombre,
            descripcion=descripcion,
        )

        log_inbound_event(
            "plantilla_proveedor_creada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor_id,
            plantilla_id=plantilla.id,
        )

        return _redirect("/inbound/proveedores/plantillas", ok="Plantilla creada.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor_id,
            error=str(e),
        )
        return _redirect("/inbound/proveedores/plantillas", error=str(e))


@router.post("/proveedores/plantillas/{plantilla_id}/estado", response_class=HTMLResponse)
async def inbound_plantilla_toggle(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    activo: str = Form(...),
):
    negocio_id = int(user["negocio_id"])
    flag = activo.lower() in ("true", "on", "1", "si", "sí")

    try:
        plantilla = cambiar_estado_plantilla_proveedor(
            db=db,
            negocio_id=negocio_id,
            plantilla_id=plantilla_id,
            activo=flag,
        )

        log_inbound_event(
            "plantilla_proveedor_estado_cambiado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla.id,
            activo=plantilla.activo,
        )

        return _redirect("/inbound/proveedores/plantillas", ok="Estado de plantilla actualizado.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla_id,
            error=str(e),
        )
        return _redirect("/inbound/proveedores/plantillas", error=str(e))
