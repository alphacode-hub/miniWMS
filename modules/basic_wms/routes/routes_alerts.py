# routes_alerts.py
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Depends,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import join  # opcional, pero no estrictamente necesario

from core.database import get_db
from core.models import Alerta, Negocio
from core.security import require_roles_dep


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#      ROUTER ALERTAS
# ============================

router = APIRouter(
    prefix="",
    tags=["alertas"],
)


# ============================
#      ALERTAS - LISTADO
# ============================

@router.get("/alertas", response_class=HTMLResponse)
async def alertas_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Centro de alertas.
    - admin       → ve las alertas de su negocio
    - superadmin  → ve alertas de todos los negocios
    """
    rol = user["rol"]

    if rol == "superadmin":
        # Vista global: todas las alertas de todos los negocios
        alertas = (
            db.query(Alerta)
            .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
            .limit(500)
            .all()
        )
    else:
        negocio_id = user["negocio_id"]
        alertas = (
            db.query(Alerta)
            .filter(Alerta.negocio_id == negocio_id)
            .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
            .limit(200)
            .all()
        )

    return templates.TemplateResponse(
        "alertas.html",
        {
            "request": request,
            "user": user,
            "alertas": alertas,
        },
    )


# ============================
#  ALERTAS - MARCAR COMO LEÍDA
# ============================

@router.post("/alertas/{alerta_id}/marcar-leida")
async def alerta_marcar_leida(
    alerta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Marca una alerta como leída.
    - admin       → sólo puede marcar alertas de su negocio
    - superadmin  → puede marcar cualquier alerta
    """
    rol = user["rol"]
    negocio_id = user["negocio_id"]

    if rol == "superadmin":
        # Superadmin puede gestionar cualquier alerta
        alerta = (
            db.query(Alerta)
            .filter(Alerta.id == alerta_id)
            .first()
        )
    else:
        # Admin solo puede gestionar alertas de su negocio
        alerta = (
            db.query(Alerta)
            .join(Negocio, Alerta.negocio_id == Negocio.id)
            .filter(
                Alerta.id == alerta_id,
                Negocio.id == negocio_id,
            )
            .first()
        )

    if not alerta:
        raise HTTPException(status_code=404, detail="Alerta no encontrada.")

    if alerta.estado == "pendiente":
        alerta.estado = "leida"
        # fecha_envio se usará cuando realmente se envíe por WhatsApp/email
        db.commit()

    return RedirectResponse(url="/alertas", status_code=302)
