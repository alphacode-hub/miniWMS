# modules/inbound_orbion/routes/routes_inbound_proveedores.py
"""
Rutas – Proveedores + Plantillas proveedor (Inbound ORBION, baseline aligned)

✔ Multi-tenant estricto (negocio_id)
✔ UX ok/error por querystring (redirect helper)
✔ Logging inbound event/error
✔ Coherente con services_inbound_proveedores.py (model-aligned)
✔ Bridge de producto rápido (no depende de WMS)
"""

from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import Producto

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from modules.inbound_orbion.services.services_inbound_productos_bridge import (
    crear_producto_rapido_inbound,
)
from modules.inbound_orbion.services.services_inbound_proveedores import (
    listar_proveedores,
    crear_proveedor,
    cambiar_estado_proveedor,
    obtener_proveedor_seguro,
    obtener_plantilla_segura,
    listar_plantillas_proveedor,
    crear_plantilla_proveedor,
    cambiar_estado_plantilla_proveedor,
    eliminar_plantilla_proveedor,
    listar_lineas_plantilla_proveedor,
    crear_linea_plantilla_proveedor,
    cambiar_estado_linea_plantilla_proveedor,
    eliminar_linea_plantilla_proveedor,
)

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


def _flag_from_form(activo: str) -> bool:
    return (activo or "").strip().lower() in ("true", "on", "1", "si", "sí")


def _to_int_or_none(v: str | None) -> int | None:
    s = (v or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _to_str_or_none(v: str | None) -> str | None:
    s = (v or "").strip()
    return s or None


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

    proveedores = listar_proveedores(db=db, negocio_id=negocio_id, solo_activos=False)

    total = len(proveedores)
    activos = sum(1 for p in proveedores if bool(getattr(p, "activo", 0)))
    inactivos = total - activos

    log_inbound_event(
        "proveedores_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total=total,
        activos=activos,
        inactivos=inactivos,
    )

    return templates.TemplateResponse(
        "inbound_proveedores.html",
        {
            "request": request,
            "user": user,
            "proveedores": proveedores,
            "total": total,
            "activos": activos,
            "inactivos": inactivos,
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
    contacto: str = Form(""),
    direccion: str = Form(""),
    observaciones: str = Form(""),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    try:
        proveedor = crear_proveedor(
            db=db,
            negocio_id=negocio_id,
            nombre=nombre,
            rut=rut,
            telefono=telefono,
            email=email,
            contacto=contacto,
            direccion=direccion,
            observaciones=observaciones,
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
    flag = _flag_from_form(activo)

    try:
        proveedor = cambiar_estado_proveedor(
            db,
            negocio_id=negocio_id,
            proveedor_id=proveedor_id,
            activo=flag,
        )

        log_inbound_event(
            "proveedor_estado_cambiado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor.id,
            activo=int(getattr(proveedor, "activo", 0) or 0),
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
# PLANTILLAS POR PROVEEDOR
# ==========================================================

@router.get("/proveedores/{proveedor_id}/plantillas", response_class=HTMLResponse)
async def inbound_proveedor_plantillas(
    proveedor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)
    plantillas = listar_plantillas_proveedor(db, negocio_id, proveedor_id=proveedor_id, solo_activas=False)

    log_inbound_event(
        "plantillas_proveedor_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        proveedor_id=proveedor_id,
        total=len(plantillas),
    )

    return templates.TemplateResponse(
        "inbound_proveedor_plantillas.html",
        {
            "request": request,
            "user": user,
            "proveedor": proveedor,
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
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    try:
        plantilla = crear_plantilla_proveedor(
            db,
            negocio_id=negocio_id,
            proveedor_id=proveedor_id,
            nombre=nombre,
        )

        log_inbound_event(
            "plantilla_proveedor_creada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor_id,
            plantilla_id=plantilla.id,
        )

        return _redirect(f"/inbound/proveedores/{proveedor_id}/plantillas", ok="Plantilla creada.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            proveedor_id=proveedor_id,
            error=str(e),
        )
        return _redirect(f"/inbound/proveedores/{proveedor_id}/plantillas", error=str(e))


@router.post("/proveedores/plantillas/{plantilla_id}/estado", response_class=HTMLResponse)
async def inbound_plantilla_toggle(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    activo: str = Form(...),
):
    negocio_id = int(user["negocio_id"])
    flag = _flag_from_form(activo)

    try:
        plantilla = cambiar_estado_plantilla_proveedor(
            db,
            negocio_id=negocio_id,
            plantilla_id=plantilla_id,
            activo=flag,
        )

        log_inbound_event(
            "plantilla_proveedor_estado_cambiado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla.id,
            activo=int(getattr(plantilla, "activo", 0) or 0),
        )

        return _redirect(
            f"/inbound/proveedores/{plantilla.proveedor_id}/plantillas",
            ok="Estado de plantilla actualizado.",
        )

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla_id,
            error=str(e),
        )
        return _redirect("/inbound/proveedores", error=str(e))


@router.post("/proveedores/plantillas/{plantilla_id}/eliminar", response_class=HTMLResponse)
async def inbound_plantilla_eliminar(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    try:
        plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
        proveedor_id = plantilla.proveedor_id

        eliminar_plantilla_proveedor(db, negocio_id=negocio_id, plantilla_id=plantilla_id)

        log_inbound_event(
            "plantilla_proveedor_eliminada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla_id,
        )

        return _redirect(f"/inbound/proveedores/{proveedor_id}/plantillas", ok="Plantilla eliminada.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_eliminar_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla_id,
            error=str(e),
        )
        return _redirect("/inbound/proveedores", error=str(e))


# ==========================================================
# DETALLE PLANTILLA + LÍNEAS
# ==========================================================

@router.get("/proveedores/plantillas/{plantilla_id}", response_class=HTMLResponse)
async def inbound_plantilla_detalle(
    plantilla_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
    proveedor = obtener_proveedor_seguro(db, negocio_id, plantilla.proveedor_id)

    lineas = listar_lineas_plantilla_proveedor(db, negocio_id, plantilla_id=plantilla_id, solo_activas=False)

    productos = (
        db.query(Producto)
        .filter(Producto.negocio_id == negocio_id)
        .order_by(Producto.nombre.asc())
        .all()
    )

    log_inbound_event(
        "plantilla_proveedor_detalle_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        plantilla_id=plantilla_id,
        total_lineas=len(lineas),
    )

    return templates.TemplateResponse(
        "inbound_proveedor_plantilla_detalle.html",
        {
            "request": request,
            "user": user,
            "proveedor": proveedor,
            "plantilla": plantilla,
            "lineas": lineas,
            "productos": productos,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/proveedores/plantillas/{plantilla_id}/lineas/nueva", response_class=HTMLResponse)
async def inbound_plantilla_linea_crear(
    plantilla_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    producto_id: str = Form(""),
    nuevo_producto_nombre: str = Form(""),
    nuevo_producto_unidad_base: str = Form(""),
    descripcion: str = Form(""),
    sku_proveedor: str = Form(""),
    ean13: str = Form(""),
    unidad: str = Form(""),
    activo: str = Form("true"),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    flag = _flag_from_form(activo)

    try:
        # valida existencia + pertenencia
        _ = obtener_plantilla_segura(db, negocio_id, plantilla_id)

        pid = _to_int_or_none(producto_id)
        nuevo_nombre = _to_str_or_none(nuevo_producto_nombre)
        nuevo_unidad = _to_str_or_none(nuevo_producto_unidad_base)

        # 1) si viene producto_id, úsalo
        if pid:
            # validar que sea del negocio (multi-tenant)
            pr = (
                db.query(Producto)
                .filter(Producto.id == pid, Producto.negocio_id == negocio_id)
                .first()
            )
            if not pr:
                raise InboundDomainError("El producto seleccionado no es válido para este negocio.")
            producto_final_id = pr.id

        # 2) si no, crea producto rápido (bridge)
        elif nuevo_nombre:
            pr = crear_producto_rapido_inbound(
                db=db,
                negocio_id=negocio_id,
                nombre=nuevo_nombre,
                unidad=(nuevo_unidad or "unidad"),
            )
            producto_final_id = pr.id

        else:
            raise InboundDomainError("Debes seleccionar un producto o ingresar un producto rápido.")

        linea = crear_linea_plantilla_proveedor(
            db,
            negocio_id=negocio_id,
            plantilla_id=plantilla_id,
            producto_id=int(producto_final_id),
            descripcion=descripcion,
            sku_proveedor=sku_proveedor,
            ean13=ean13,
            unidad=unidad,
            activo=flag,
        )

        log_inbound_event(
            "plantilla_proveedor_linea_creada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla_id,
            linea_id=linea.id,
            producto_id=int(producto_final_id),
            created_via=("producto_id" if pid else "producto_rapido"),
        )

        return _redirect(f"/inbound/proveedores/plantillas/{plantilla_id}", ok="Línea agregada.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_linea_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            plantilla_id=plantilla_id,
            error=str(e),
        )
        return _redirect(f"/inbound/proveedores/plantillas/{plantilla_id}", error=str(e))


@router.post("/proveedores/plantillas/lineas/{linea_id}/estado", response_class=HTMLResponse)
async def inbound_plantilla_linea_toggle(
    linea_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    activo: str = Form(...),
    plantilla_id: int = Form(...),
):
    negocio_id = int(user["negocio_id"])
    flag = _flag_from_form(activo)

    try:
        linea = cambiar_estado_linea_plantilla_proveedor(
            db,
            negocio_id=negocio_id,
            linea_id=linea_id,
            activo=flag,
        )

        log_inbound_event(
            "plantilla_proveedor_linea_estado_cambiado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            linea_id=linea.id,
            activo=int(getattr(linea, "activo", 0) or 0),
        )

        return _redirect(f"/inbound/proveedores/plantillas/{plantilla_id}", ok="Estado de línea actualizado.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_linea_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            linea_id=linea_id,
            error=str(e),
        )
        return _redirect(f"/inbound/proveedores/plantillas/{plantilla_id}", error=str(e))


@router.post("/proveedores/plantillas/lineas/{linea_id}/eliminar", response_class=HTMLResponse)
async def inbound_plantilla_linea_eliminar(
    linea_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
    plantilla_id: int = Form(...),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    try:
        eliminar_linea_plantilla_proveedor(db, negocio_id=negocio_id, linea_id=linea_id)

        log_inbound_event(
            "plantilla_proveedor_linea_eliminada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            linea_id=linea_id,
        )

        return _redirect(f"/inbound/proveedores/plantillas/{plantilla_id}", ok="Línea eliminada.")

    except InboundDomainError as e:
        log_inbound_error(
            "plantilla_linea_eliminar_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            linea_id=linea_id,
            error=str(e),
        )
        return _redirect(f"/inbound/proveedores/plantillas/{plantilla_id}", error=str(e))
