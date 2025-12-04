# routes_slots.py
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
from core.models import Slot, Ubicacion, Zona
from core.security import require_roles_dep
from core.services.services_plan_limits import check_plan_limit


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER SLOTS
# ============================

router = APIRouter(
    prefix="",
    tags=["slots"],
)


# ============================
#     SLOTS
# ============================

@router.get("/ubicaciones/{ubicacion_id}/slots", response_class=HTMLResponse)
async def slots_list(
    ubicacion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Lista los slots de una ubicación específica.
    - admin: solo puede ver ubicaciones de su negocio
    - superadmin: puede ver slots de cualquier ubicación
    """
    if user["rol"] == "superadmin":
        # Superadmin puede ver cualquier ubicación
        ubicacion = (
            db.query(Ubicacion)
            .join(Zona, Ubicacion.zona_id == Zona.id)
            .filter(Ubicacion.id == ubicacion_id)
            .first()
        )
    else:
        # Admin solo puede ver ubicaciones de su negocio
        negocio_id = user["negocio_id"]
        ubicacion = (
            db.query(Ubicacion)
            .join(Zona, Ubicacion.zona_id == Zona.id)
            .filter(
                Ubicacion.id == ubicacion_id,
                Zona.negocio_id == negocio_id,
            )
            .first()
        )

    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    slots = (
        db.query(Slot)
        .filter(Slot.ubicacion_id == ubicacion.id)
        .order_by(Slot.codigo.asc())
        .all()
    )

    return templates.TemplateResponse(
        "slots.html",
        {
            "request": request,
            "user": user,
            "ubicacion": ubicacion,
            "slots": slots,
        },
    )


@router.get("/ubicaciones/{ubicacion_id}/slots/nuevo", response_class=HTMLResponse)
async def slot_nuevo_form(
    ubicacion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Formulario para crear un nuevo slot en una ubicación.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    # Admin solo puede crear slots en ubicaciones de su negocio
    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "slot_nuevo.html",
        {
            "request": request,
            "user": user,
            "ubicacion": ubicacion,
            "error": None,
            "codigo": "",
            "capacidad": "",
        },
    )


@router.post("/ubicaciones/{ubicacion_id}/slots/nuevo", response_class=HTMLResponse)
async def slot_nuevo_submit(
    ubicacion_id: int,
    request: Request,
    codigo: str = Form(...),
    capacidad: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin")),
):
    """
    Procesa la creación de un nuevo slot en una ubicación.
    Solo admin del negocio.
    """
    negocio_id = user["negocio_id"]

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    codigo = (codigo or "").strip().upper()
    capacidad_str = (capacidad or "").strip()

    # Validación: código obligatorio
    if not codigo:
        return templates.TemplateResponse(
            "slot_nuevo.html",
            {
                "request": request,
                "user": user,
                "ubicacion": ubicacion,
                "error": "El código del slot no puede estar vacío.",
                "codigo": codigo,
                "capacidad": capacidad_str,
            },
            status_code=400,
        )

    # Validar duplicado de código dentro de la misma ubicación
    existe = (
        db.query(Slot)
        .filter(
            Slot.ubicacion_id == ubicacion.id,
            func.lower(Slot.codigo) == codigo.lower(),
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "slot_nuevo.html",
            {
                "request": request,
                "user": user,
                "ubicacion": ubicacion,
                "error": f"Ya existe un slot '{codigo}' en esta ubicación.",
                "codigo": codigo,
                "capacidad": capacidad_str,
            },
            status_code=400,
        )

    # Parseo de capacidad (opcional)
    capacidad_int = None
    if capacidad_str.isdigit():
        capacidad_int = int(capacidad_str)

    # Aplicar límite de plan
    check_plan_limit(db, negocio_id, "slots")

    # Construcción de código completo usando siglas reales de zona/ubicación
    zona_sigla = (ubicacion.zona.sigla or ubicacion.zona.nombre[:1]).upper()
    ubic_sigla = (ubicacion.sigla or "".join(p[0] for p in ubicacion.nombre.split())).upper()
    codigo_full = f"{zona_sigla}-{ubic_sigla}-{codigo}"

    slot = Slot(
        ubicacion_id=ubicacion.id,
        codigo=codigo,
        capacidad=capacidad_int,
        codigo_full=codigo_full,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)

    print(f">>> NUEVO SLOT: {slot.codigo_full}")

    return RedirectResponse(
        url=f"/ubicaciones/{ubicacion.id}/slots",
        status_code=302,
    )
