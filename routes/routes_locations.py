# routes_locations.py
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

from database import get_db
from models import Zona, Ubicacion
from security import require_roles_dep
from services.services_plan_limits import check_plan_limit


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER UBICACIONES
# ============================

router = APIRouter(
    prefix="",
    tags=["ubicaciones"],
)


# ============================
#     UBICACIONES
# ============================

@router.get("/zonas/{zona_id}/ubicaciones", response_class=HTMLResponse)
async def ubicaciones_list(
    zona_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Lista las ubicaciones de una zona específica.
    - admin: solo puede ver zonas de su negocio
    - superadmin: puede ver ubicaciones de cualquier zona
    """
    if user["rol"] == "superadmin":
        # Superadmin puede ver cualquier zona
        zona = (
            db.query(Zona)
            .filter(Zona.id == zona_id)
            .first()
        )
    else:
        # Admin solo puede ver zonas de su negocio
        negocio_id = user["negocio_id"]
        zona = (
            db.query(Zona)
            .filter(
                Zona.id == zona_id,
                Zona.negocio_id == negocio_id,
            )
            .first()
        )

    if not zona:
        # Zona no encontrada o no pertenece al negocio del admin
        return RedirectResponse("/zonas", status_code=302)

    ubicaciones = (
        db.query(Ubicacion)
        .filter(Ubicacion.zona_id == zona.id)
        .order_by(Ubicacion.nombre.asc())
        .all()
    )

    return templates.TemplateResponse(
        "ubicaciones.html",
        {
            "request": request,
            "user": user,
            "zona": zona,
            "ubicaciones": ubicaciones,
        },
    )


@router.get("/zonas/{zona_id}/ubicaciones/nueva", response_class=HTMLResponse)
async def ubicacion_nueva_form(
    zona_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Formulario para crear una nueva ubicación dentro de una zona.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    if not zona:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "ubicacion_nueva.html",
        {
            "request": request,
            "user": user,
            "zona": zona,
            "error": None,
            "nombre": "",
            "sigla": "",
        },
    )


@router.post("/zonas/{zona_id}/ubicaciones/nueva", response_class=HTMLResponse)
async def ubicacion_nueva_submit(
    zona_id: int,
    request: Request,
    nombre: str = Form(...),
    sigla: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Procesa la creación de una nueva ubicación en una zona.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    if not zona:
        return RedirectResponse("/zonas", status_code=302)

    nombre = (nombre or "").strip()
    sigla = (sigla or "").strip().upper()

    # Validación: nombre obligatorio
    if not nombre:
        return templates.TemplateResponse(
            "ubicacion_nueva.html",
            {
                "request": request,
                "user": user,
                "zona": zona,
                "error": "El nombre de la ubicación no puede estar vacío.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    # Si no se entrega sigla, se genera a partir de las iniciales del nombre
    if not sigla:
        partes = nombre.split()
        sigla = "".join(p[0] for p in partes).upper()

    # Validar duplicado dentro de la misma zona (case-insensitive)
    existe = (
        db.query(Ubicacion)
        .filter(
            Ubicacion.zona_id == zona.id,
            func.lower(Ubicacion.nombre) == nombre.lower(),
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "ubicacion_nueva.html",
            {
                "request": request,
                "user": user,
                "zona": zona,
                "error": f"Ya existe una ubicación '{nombre}' en esta zona.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    # Aplicar límite de plan
    check_plan_limit(db, negocio_id, "ubicaciones")

    ubicacion = Ubicacion(
        zona_id=zona.id,
        nombre=nombre,
        sigla=sigla,
    )
    db.add(ubicacion)
    db.commit()
    db.refresh(ubicacion)

    print(">>> NUEVA UBICACION:", ubicacion.id, ubicacion.nombre, ubicacion.sigla)

    return RedirectResponse(
        url=f"/zonas/{zona.id}/ubicaciones",
        status_code=302,
    )
