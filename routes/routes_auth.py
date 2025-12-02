# routes_auth.py
from pathlib import Path
import json
from datetime import datetime

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Usuario, SesionUsuario
from security import (
    get_current_user,
    is_superadmin,
    verify_password,
    crear_sesion_db,
    crear_cookie_sesion,
    signer,
)

# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ============================
#   ROUTER AUTH / HOME
# ============================

router = APIRouter(
    prefix="",          # sin prefijo, mantiene /, /login, /logout
    tags=["auth"],
)


# ============================
# HOME / LOGIN / LOGOUT
# ============================

@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if is_superadmin(user):
        return RedirectResponse(url="/superadmin/dashboard", status_code=302)

    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        # Si ya está autenticado, lo mandamos a su panel
        if is_superadmin(user):
            return RedirectResponse(url="/superadmin/dashboard", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "user": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = (email or "").strip().lower()

    usuario = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )

    # Validar existencia y estado
    if not usuario or usuario.activo != 1:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Correo o contraseña incorrectos.", "user": None},
            status_code=401,
        )

    # Validar contraseña con bcrypt
    if not verify_password(password, usuario.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Correo o contraseña incorrectos.", "user": None},
            status_code=401,
        )

    # (Opcional) Validar estado del negocio
    if usuario.negocio and usuario.negocio.estado != "activo":
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "El negocio asociado a este usuario está suspendido.",
                "user": None
            },
            status_code=403,
        )

    # Login OK → crear sesión en BD
    token_sesion = crear_sesion_db(db, usuario)

    # Crear respuesta de redirección según rol
    if usuario.rol == "superadmin":
        redirect_url = "/superadmin/dashboard"
    else:
        redirect_url = "/dashboard"

    response = RedirectResponse(url=redirect_url, status_code=302)
    crear_cookie_sesion(response, usuario, token_sesion)
    return response


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, db: Session = Depends(get_db)):
    # 1) Leer cookie firmada
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)

    if cookie:
        try:
            data = signer.unsign(cookie).decode("utf-8")
            payload = json.loads(data)
            user_id = payload.get("user_id")
            token_sesion = payload.get("token_sesion")

            # 2) Marcar sesión como inactiva en BD
            if user_id and token_sesion:
                db.query(SesionUsuario).filter(
                    SesionUsuario.usuario_id == user_id,
                    SesionUsuario.token_sesion == token_sesion,
                    SesionUsuario.activo == 1,
                ).update({SesionUsuario.activo: 0})
                db.commit()
        except (BadSignature, json.JSONDecodeError):
            # Si la cookie es inválida, simplemente seguimos y la borramos igual
            pass

    # 3) Redirigir a login y borrar cookie
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return response
