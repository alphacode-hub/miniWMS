# routes_zones.py
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

from core.database import get_db
from core.models import Zona
from core.security import require_roles_dep
from core.services.services_plan_limits import check_plan_limit


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER ZONAS
# ============================

router = APIRouter(
    prefix="",
    tags=["zonas"],
)


# ============================
#     ZONAS
# ============================

@router.get("/zonas", response_class=HTMLResponse)
async def zonas_list(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Lista las zonas del negocio actual.
    - admin: ve las zonas de su negocio
    - superadmin: ve todas las zonas
    """
    if user["rol"] == "superadmin":
        zonas = (
            db.query(Zona)
            .order_by(Zona.nombre.asc())
            .all()
        )
    else:
        negocio_id = user["negocio_id"]
        zonas = (
            db.query(Zona)
            .filter(Zona.negocio_id == negocio_id)
            .order_by(Zona.nombre.asc())
            .all()
        )

    return templates.TemplateResponse(
        "zonas.html",
        {
            "request": request,
            "user": user,
            "zonas": zonas,
        },
    )


@router.get("/zonas/nueva", response_class=HTMLResponse)
async def zona_nueva_form(
    request: Request,
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Formulario para crear una nueva zona en el negocio actual.
    Solo admin del negocio.
    """
    return templates.TemplateResponse(
        "zona_nueva.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "nombre": "",
            "sigla": "",
        },
    )


@router.post("/zonas/nueva", response_class=HTMLResponse)
async def zona_nueva_submit(
    request: Request,
    nombre: str = Form(...),
    sigla: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Procesa la creación de una nueva zona.
    Solo admin del negocio.
    """
    nombre = (nombre or "").strip()
    sigla = (sigla or "").strip().upper()
    negocio_id = user["negocio_id"]

    # Validación: nombre obligatorio
    if not nombre:
        return templates.TemplateResponse(
            "zona_nueva.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre de la zona no puede estar vacío.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    # Si no se entrega sigla, usamos la primera letra del nombre
    if not sigla:
        sigla = nombre[:1].upper()

    # Validar que no exista ya la misma zona en ese negocio (case-insensitive)
    existe = (
        db.query(Zona)
        .filter(
            Zona.negocio_id == negocio_id,
            func.lower(Zona.nombre) == nombre.lower(),
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "zona_nueva.html",
            {
                "request": request,
                "user": user,
                "error": f"Ya existe una zona con el nombre '{nombre}'.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    # Aplicar límite de plan
    check_plan_limit(db, negocio_id, "zonas")

    zona = Zona(
        negocio_id=negocio_id,
        nombre=nombre,
        sigla=sigla,
    )
    db.add(zona)
    db.commit()
    db.refresh(zona)

    print(">>> NUEVA ZONA:", zona.id, zona.nombre, zona.sigla)

    return RedirectResponse(url="/zonas", status_code=302)
