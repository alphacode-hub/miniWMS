# core/routes/routes_business.py
"""
Registro de negocio (signup) – ORBION (SaaS enterprise)

✔ Registro de negocio + usuario admin
✔ Validaciones robustas
✔ Unicidad case-insensitive
✔ Manejo seguro de errores (sin print)
✔ Auto-login post-registro
✔ Baseline aligned:
  - Negocio.entitlements default (fuente única)
  - Negocio.estado como Enum (NegocioEstado)
  - plan_tipo legacy (fallback interno, entitlements manda)
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from core.web import templates
from core.database import get_db
from core.logging_config import logger
from core.models import Negocio, Usuario
from core.models.enums import NegocioEstado
from core.security import (
    get_current_user,
    hash_password,
    crear_sesion_db,
    crear_cookie_sesion,
)


# ============================
# ROUTER
# ============================

router = APIRouter(
    prefix="/app",
    tags=["registro_negocio"],
)


# ============================
# GET
# ============================

@router.get("/registrar-negocio", response_class=HTMLResponse)
async def registrar_negocio_get(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/app", status_code=302)

    return templates.TemplateResponse(
        "app/registrar_negocio.html",
        {
            "request": request,
            "errores": [],
            "nombre_negocio": "",
            "nombre_admin": "",
            "whatsapp": "",
            "email": "",
        },
    )


# ============================
# POST
# ============================

@router.post("/registrar-negocio", response_class=HTMLResponse)
async def registrar_negocio_post(
    request: Request,
    nombre_negocio: str = Form(...),
    nombre_admin: str = Form(...),
    whatsapp: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    errores: list[str] = []

    nombre_negocio = (nombre_negocio or "").strip()
    nombre_admin = (nombre_admin or "").strip()
    whatsapp = (whatsapp or "").strip()
    email_norm = (email or "").strip().lower()
    password = password or ""
    password2 = password2 or ""

    # ----------------------------
    # Validaciones base
    # ----------------------------
    if len(nombre_negocio) < 3:
        errores.append("El nombre del negocio es muy corto (mínimo 3 caracteres).")

    if len(nombre_admin) < 3:
        errores.append("El nombre del administrador es muy corto (mínimo 3 caracteres).")

    # Validación simple de email (sin dependencias)
    if (
        " " in email_norm
        or "@" not in email_norm
        or "." not in email_norm
        or email_norm.startswith("@")
        or email_norm.endswith("@")
        or email_norm.endswith(".")
    ):
        errores.append("Debes ingresar un correo válido.")

    if len(password) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")

    if password != password2:
        errores.append("Las contraseñas no coinciden.")

    # ----------------------------
    # Unicidad (case-insensitive)
    # ----------------------------

    if email_norm:
        existing_user = (
            db.query(Usuario)
            .filter(func.lower(Usuario.email) == email_norm)
            .first()
        )
        if existing_user:
            errores.append("Ya existe un usuario registrado con ese correo.")

    if nombre_negocio:
        existing_neg = (
            db.query(Negocio)
            .filter(func.lower(Negocio.nombre_fantasia) == nombre_negocio.lower())
            .first()
        )
        if existing_neg:
            errores.append("Ya existe un negocio con ese nombre de fantasía.")

    if errores:
        return templates.TemplateResponse(
            "app/registrar_negocio.html",
            {
                "request": request,
                "errores": errores,
                "nombre_negocio": nombre_negocio,
                "nombre_admin": nombre_admin,
                "whatsapp": whatsapp,
                "email": email_norm,
            },
            status_code=400,
        )

    # ----------------------------
    # Crear negocio + admin
    # ----------------------------
    try:
        negocio = Negocio(
            nombre_fantasia=nombre_negocio,
            whatsapp_notificaciones=whatsapp or None,
            estado=NegocioEstado.ACTIVO,  # ✅ enum aligned
            plan_tipo="legacy",           # ✅ entitlements manda; plan_tipo solo fallback interno
        )
        db.add(negocio)
        db.flush()  # obtiene negocio.id

        usuario_admin = Usuario(
            negocio_id=negocio.id,
            email=email_norm,
            password_hash=hash_password(password),
            rol="admin",
            activo=1,
            nombre_mostrado=nombre_admin,
        )
        db.add(usuario_admin)
        db.commit()

        # defensivo: asegura ids/attrs refrescados
        db.refresh(negocio)
        db.refresh(usuario_admin)

        logger.info(
            "[SIGNUP] Negocio creado id=%s nombre=%s admin=%s",
            negocio.id,
            nombre_negocio,
            email_norm,
        )

    except Exception as exc:
        db.rollback()
        logger.exception("[SIGNUP] Error creando negocio/usuario: %s", exc)

        return templates.TemplateResponse(
            "app/registrar_negocio.html",
            {
                "request": request,
                "errores": ["Ocurrió un error al crear el negocio. Inténtalo nuevamente."],
                "nombre_negocio": nombre_negocio,
                "nombre_admin": nombre_admin,
                "whatsapp": whatsapp,
                "email": email_norm,
            },
            status_code=500,
        )

    # ----------------------------
    # Auto-login del admin
    # ----------------------------
    token_sesion = crear_sesion_db(db, usuario_admin)
    response = RedirectResponse(url="/app", status_code=302)
    crear_cookie_sesion(response, usuario_admin, token_sesion)
    return response
