# core/routes/routes_auth.py
"""
Auth & Session routes – ORBION (SaaS enterprise)

✔ Login centralizado en /app/login
✔ Logout seguro (revoca sesión en BD)
✔ Redirección única post-login (/app)
✔ Manejo robusto de cookies firmadas
✔ Templates SSR (Jinja2)
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from itsdangerous import BadSignature
from core.web import templates


from core.config import settings
from core.database import get_db
from core.models import Usuario, SesionUsuario
from core.security import (
    get_current_user,
    verify_password,
    crear_sesion_db,
    crear_cookie_sesion,
    signer,
    SESSION_INACTIVITY_SECONDS,
)
from core.logging_config import logger



# ============================
# ROUTER
# ============================

router = APIRouter(
    prefix="/app",
    tags=["auth"],
)


# ============================
# LOGIN
# ============================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    Página de login.
    """
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/app", status_code=302)

    return templates.TemplateResponse(
        "app/login.html",
        {
            "request": request,
            "error": None,
            "user": None,
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Procesa login.
    """
    email_norm = (email or "").strip().lower()

    usuario = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )

    # Usuario válido
    if not usuario or usuario.activo != 1:
        return templates.TemplateResponse(
            "app/login.html",
            {
                "request": request,
                "error": "Correo o contraseña incorrectos.",
                "user": None,
                "email": email_norm,
            },
            status_code=401,
        )

    # Password
    if not verify_password(password, usuario.password_hash):
        return templates.TemplateResponse(
            "app/login.html",
            {
                "request": request,
                "error": "Correo o contraseña incorrectos.",
                "user": None,
                "email": email_norm
            },
            status_code=401,
        )

    # Estado del negocio (si aplica)
    if usuario.negocio and usuario.negocio.estado != "activo":
        return templates.TemplateResponse(
            "app/login.html",
            {
                "request": request,
                "error": "El negocio asociado está suspendido.",
                "user": None,
            },
            status_code=403,
        )

    # Crear sesión
    token_sesion = crear_sesion_db(db, usuario)

    response = RedirectResponse(url="/app", status_code=302)
    crear_cookie_sesion(response, usuario, token_sesion)

    logger.info("[AUTH] Login OK user=%s rol=%s", usuario.email, usuario.rol)
    return response


# ============================
# LOGOUT
# ============================

@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, db: Session = Depends(get_db)):
    """
    Logout:
    - Revoca sesión activa en BD
    - Elimina cookie
    """
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)

    if cookie:
        try:
            raw = signer.unsign(cookie, max_age=SESSION_INACTIVITY_SECONDS)
            data = raw.decode("utf-8", errors="ignore")
            payload = json.loads(data)

            user_id = payload.get("user_id")
            token_sesion = payload.get("token_sesion")

            if user_id and token_sesion:
                db.query(SesionUsuario).filter(
                    SesionUsuario.usuario_id == user_id,
                    SesionUsuario.token_sesion == token_sesion,
                    SesionUsuario.activo == 1,
                ).update({SesionUsuario.activo: 0})
                db.commit()

                logger.info("[AUTH] Logout user_id=%s", user_id)

        except BadSignature:
            logger.debug("[AUTH] Cookie inválida en logout")
        except Exception as exc:
            logger.warning("[AUTH] Error en logout: %s", exc)

    response = RedirectResponse(url="/app/login", status_code=302)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
    )
    return response
