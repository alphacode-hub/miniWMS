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
    is_superadmin_global,
    verify_password,
    crear_sesion_db,
    crear_cookie_sesion,
    signer,
    SESSION_INACTIVITY_SECONDS,
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

    # Si es superadmin "puro" (sin modo negocio) → dashboard global
    if user.get("rol_real") == "superadmin" and not user.get("impersonando_negocio_id"):
        return RedirectResponse(url="/superadmin/dashboard", status_code=302)

    # Admin, operador o superadmin en modo negocio → dashboard del negocio
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        # Si ya está autenticado, lo mandamos a su panel
        if is_superadmin_global(user):
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

    # Validar estado del negocio
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

    # Crear respuesta de redirección según rol REAL
    if usuario.rol == "superadmin":
        redirect_url = "/superadmin/dashboard"
    else:
        redirect_url = "/dashboard"

    response = RedirectResponse(url=redirect_url, status_code=302)
    crear_cookie_sesion(response, usuario, token_sesion)
    return response


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, db: Session = Depends(get_db)):
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)

    if cookie:
        try:
            data = signer.unsign(cookie, max_age=SESSION_INACTIVITY_SECONDS).decode("utf-8")
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
        except Exception:
            # Cookie inválida o expirada: la ignoramos y seguimos
            pass

    response = RedirectResponse(url="/login", status_code=302)
    # Nos aseguramos de borrar la cookie en el path raíz
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
    )
    return response
