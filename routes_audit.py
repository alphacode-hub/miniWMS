# routes_audit.py
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Depends,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Auditoria
from security import require_roles_dep


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#      ROUTER AUDITORÍA
# ============================

router = APIRouter(
    prefix="",
    tags=["auditoria"],
)


# ============================
#      AUDITORIA
# ============================

@router.get("/auditoria", response_class=HTMLResponse)
async def auditoria_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "superadmin")),
):
    """
    Vista de auditoría.
    - admin       → ve sólo la auditoría de su negocio
    - superadmin  → ve auditoría global (todos los negocios)
    """
    rol = user["rol"]

    if rol == "superadmin":
        # Superadmin ve todo
        registros = (
            db.query(Auditoria)
            .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
            .limit(500)
            .all()
        )
    else:
        # Admin ve sólo auditoría de su negocio (usando FK negocio_id)
        negocio_id = user["negocio_id"]

        registros = (
            db.query(Auditoria)
            .filter(Auditoria.negocio_id == negocio_id)
            .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
            .limit(200)
            .all()
        )

    return templates.TemplateResponse(
        "auditoria.html",
        {
            "request": request,
            "user": user,
            "registros": registros,
        },
    )
