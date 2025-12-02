# routes_superadmin.py
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Negocio, Producto, Movimiento, Alerta, Usuario
from security import require_superadmin_dep
from plans import PLANES_CONFIG


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER SUPERADMIN
# ============================

router = APIRouter(
    prefix="/superadmin",
    tags=["superadmin"],
)


# ============================
# SUPERADMIN
# ============================

@router.get("/dashboard", response_class=HTMLResponse)
async def superadmin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    # Totales de negocios
    total_negocios = db.query(Negocio).count()
    negocios_activos = db.query(Negocio).filter(Negocio.estado == "activo").count()
    negocios_suspendidos = db.query(Negocio).filter(Negocio.estado == "suspendido").count()

    # Alertas pendientes (todas)
    alertas_pendientes = db.query(Alerta).filter(Alerta.estado == "pendiente").count()

    return templates.TemplateResponse(
        "superadmin_dashboard.html",
        {
            "request": request,
            "user": user,
            "total_negocios": total_negocios,
            "negocios_activos": negocios_activos,
            "negocios_suspendidos": negocios_suspendidos,
            "alertas_pendientes": alertas_pendientes,
        },
    )


@router.get("/negocios", response_class=HTMLResponse)
async def superadmin_negocios(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocios = db.query(Negocio).all()
    data: list[dict] = []

    for n in negocios:
        usuarios = db.query(Usuario).filter(Usuario.negocio_id == n.id).count()
        productos = db.query(Producto).filter(Producto.negocio_id == n.id).count()

        hace_30 = datetime.utcnow() - timedelta(days=30)
        movimientos = (
            db.query(Movimiento)
            .filter(
                Movimiento.negocio_id == n.id,
                Movimiento.fecha >= hace_30,
            )
            .count()
        )

        data.append(
            {
                "id": n.id,
                "nombre": n.nombre_fantasia,
                "plan": n.plan_tipo,
                "estado": n.estado,
                "usuarios": usuarios,
                "productos": productos,
                "movimientos_30d": movimientos,
                "ultimo_acceso": n.ultimo_acceso,
            }
        )

    return templates.TemplateResponse(
        "superadmin_negocios.html",
        {
            "request": request,
            "user": user,
            "negocios": data,
        },
    )


@router.get("/negocios/{negocio_id}", response_class=HTMLResponse)
async def superadmin_negocio_detalle(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    return templates.TemplateResponse(
        "superadmin_negocio_detalle.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "planes": PLANES_CONFIG.keys(),
        },
    )


@router.post("/negocios/{negocio_id}/update")
async def superadmin_negocio_update(
    request: Request,
    negocio_id: int,
    plan_tipo: str = Form(...),
    estado: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    negocio.plan_tipo = plan_tipo
    negocio.estado = estado
    db.commit()

    return RedirectResponse(
        url=f"/superadmin/negocios/{negocio_id}",
        status_code=302,
    )


@router.get("/alertas", response_class=HTMLResponse)
async def superadmin_alertas(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    alertas = (
        db.query(Alerta)
        .join(Negocio, Alerta.negocio_id == Negocio.id)
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
        .limit(500)
        .all()
    )

    return templates.TemplateResponse(
        "superadmin_alertas.html",
        {
            "request": request,
            "user": user,
            "alertas": alertas,
        },
    )
