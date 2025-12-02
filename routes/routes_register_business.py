# routes_register_business.py
from pathlib import Path
from typing import List

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
from models import Negocio, Usuario
from security import (
    get_current_user,
    is_superadmin,
    hash_password,
    crear_sesion_db,
    crear_cookie_sesion,
)

# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ============================
#   ROUTER REGISTRO NEGOCIO
# ============================

router = APIRouter(
    prefix="",
    tags=["registro_negocio"],
)


# ============================
#     REGISTRAR NEGOCIO
# ============================

@router.get("/registrar-negocio", response_class=HTMLResponse)
async def registrar_negocio_get(request: Request):
    """
    Muestra el formulario para que un dueño cree su negocio + usuario admin.
    Si ya está autenticado, lo mandamos a su dashboard.
    """
    user = get_current_user(request)
    if user:
        if is_superadmin(user):
            return RedirectResponse(url="/superadmin/dashboard", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        "registrar_negocio.html",
        {
            "request": request,
            "errores": [],
            "nombre_negocio": "",
            "whatsapp": "",
            "email": "",
        },
    )


@router.post("/registrar-negocio", response_class=HTMLResponse)
async def registrar_negocio_post(
    request: Request,
    nombre_negocio: str = Form(...),
    whatsapp: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    errores: list[str] = []

    nombre_negocio = (nombre_negocio or "").strip()
    whatsapp = (whatsapp or "").strip()
    email_norm = (email or "").strip().lower()
    password = password or ""
    password2 = password2 or ""

    # Validaciones básicas
    if len(nombre_negocio) < 3:
        errores.append("El nombre del negocio es muy corto (mínimo 3 caracteres).")

    if " " in email_norm or "@" not in email_norm:
        errores.append("Debes ingresar un correo válido.")

    if len(password) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")

    if password != password2:
        errores.append("Las contraseñas no coinciden.")

    # Validar unicidad de email
    existing_user = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )
    if existing_user:
        errores.append("Ya existe un usuario registrado con ese correo.")

    # Validar unicidad de nombre de negocio (case-insensitive)
    existing_neg = (
        db.query(Negocio)
        .filter(func.lower(Negocio.nombre_fantasia) == nombre_negocio.lower())
        .first()
    )
    if existing_neg:
        errores.append("Ya existe un negocio con ese nombre de fantasía.")

    if errores:
        # Volver a mostrar el formulario con mensajes
        return templates.TemplateResponse(
            "registrar_negocio.html",
            {
                "request": request,
                "errores": errores,
                "nombre_negocio": nombre_negocio,
                "whatsapp": whatsapp,
                "email": email_norm,
            },
            status_code=400,
        )

    # Crear negocio + usuario admin
    try:
        negocio = Negocio(
            nombre_fantasia=nombre_negocio,
            whatsapp_notificaciones=whatsapp or None,
            estado="activo",
            # opcional: si quieres fijar explícitamente el plan:
            # plan_tipo="demo",
        )
        db.add(negocio)
        db.flush()  # para obtener negocio.id

        usuario_admin = Usuario(
            negocio_id=negocio.id,
            email=email_norm,
            password_hash=hash_password(password),
            rol="admin",
            activo=1,
            nombre_mostrado=None,
        )
        db.add(usuario_admin)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[REGISTRAR_NEGOCIO] Error al crear negocio/usuario: {e}")
        return templates.TemplateResponse(
            "registrar_negocio.html",
            {
                "request": request,
                "errores": ["Ocurrió un error al crear el negocio. Inténtalo nuevamente."],
                "nombre_negocio": nombre_negocio,
                "whatsapp": whatsapp,
                "email": email_norm,
            },
            status_code=500,
        )

    # Login automático del admin recién creado
    token_sesion = crear_sesion_db(db, usuario_admin)
    response = RedirectResponse(url="/dashboard", status_code=302)
    crear_cookie_sesion(response, usuario_admin, token_sesion)
    return response
