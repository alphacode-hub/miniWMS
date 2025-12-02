# routes_users.py
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

from database import get_db
from models import Usuario
from security import require_roles_dep, hash_password
from services.services_plan_limits import check_plan_limit


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER USUARIOS
# ============================

router = APIRouter(
    prefix="",
    tags=["usuarios"],
)


# ============================
#     REGISTRAR USUARIOS
# ============================

@router.get("/usuarios", response_class=HTMLResponse)
async def listar_usuarios(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Lista usuarios (equipo) del negocio actual.
    - admin: ve usuarios de su negocio
    - superadmin: ve todos los usuarios
    """
    if user["rol"] == "superadmin":
        usuarios = (
            db.query(Usuario)
            .order_by(Usuario.id)
            .all()
        )
    else:
        usuarios = (
            db.query(Usuario)
            .filter(Usuario.negocio_id == user["negocio_id"])
            .order_by(Usuario.id)
            .all()
        )

    return templates.TemplateResponse(
        "usuarios.html",
        {
            "request": request,
            "user": user,
            "usuarios": usuarios,
        },
    )


@router.get("/usuarios/nuevo", response_class=HTMLResponse)
async def nuevo_usuario_get(
    request: Request,
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Formulario para crear un nuevo usuario (operador) del negocio actual.
    Solo admin o superadmin.
    """
    return templates.TemplateResponse(
        "usuarios_nuevo.html",
        {
            "request": request,
            "user": user,
            "errores": [],
            "nombre": "",
            "email": "",
        },
    )


@router.post("/usuarios/nuevo", response_class=HTMLResponse)
async def nuevo_usuario_post(
    request: Request,
    nombre: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Procesa la creación de un nuevo usuario operador.
    - Solo admin o superadmin.
    - Aplica límites de plan por negocio.
    """
    errores: list[str] = []

    nombre = (nombre or "").strip()
    email_norm = (email or "").strip().lower()
    password = password or ""
    password2 = password2 or ""

    # Validaciones básicas
    if " " in email_norm or "@" not in email_norm:
        errores.append("Debes ingresar un correo válido.")

    if len(password) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")

    if password != password2:
        errores.append("Las contraseñas no coinciden.")

    # Validar que no exista otro usuario con el mismo correo
    existing = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )
    if existing:
        errores.append("Ya existe un usuario registrado con ese correo.")

    if errores:
        return templates.TemplateResponse(
            "usuarios_nuevo.html",
            {
                "request": request,
                "user": user,
                "errores": errores,
                "nombre": nombre,
                "email": email_norm,
            },
            status_code=400,
        )

    # Negocio al que pertenecerá el operador
    negocio_id = user["negocio_id"]

    # Aplicar límite de plan
    check_plan_limit(db, negocio_id, "usuarios")

    try:
        nuevo = Usuario(
            negocio_id=negocio_id,
            email=email_norm,
            password_hash=hash_password(password),
            rol="operador",
            activo=1,
            nombre_mostrado=nombre or None,
        )
        db.add(nuevo)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[USUARIOS_NUEVO] Error al crear operador: {e}")
        errores.append("Ocurrió un error al crear el usuario. Inténtalo nuevamente.")
        return templates.TemplateResponse(
            "usuarios_nuevo.html",
            {
                "request": request,
                "user": user,
                "errores": errores,
                "nombre": nombre,
                "email": email_norm,
            },
            status_code=500,
        )

    return RedirectResponse("/usuarios", status_code=302)
