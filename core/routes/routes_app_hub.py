# core/routes/routes_app_hub.py

from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import require_user_dep
from core.models import Negocio
from core.services.services_modules import (
    negocio_tiene_core_wms,
    negocio_tiene_inbound,
)
from core.plans import get_inbound_plan_config

# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ============================
#   ROUTER APP HUB
# ============================

router = APIRouter(
    prefix="/app",
    tags=["app-hub"],
)


@router.get("", response_class=HTMLResponse)
async def orbion_hub_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    """
    Hub de módulos ORBION:
    - Superadmin: ve todos los módulos activos + consola superadmin.
    - Admin: ve los módulos activos según el plan de su negocio.
    - Operador: ve solo los módulos habilitados para su negocio.
    """

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")

    # ============================
    #  Negocio asociado (para admin / operador)
    # ============================
    negocio = None
    negocio_id = user.get("negocio_id")
    if negocio_id:
        negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()

    # ============================
    #  Definición base de módulos
    # ============================

    base_modules = [
        {
            "slug": "core_wms",
            "label": "ORBION Core WMS",
            "description": "Inventario, ubicaciones, rotación y auditoría.",
            "url": "/dashboard",  # dashboard actual del negocio
        },
        {
            "slug": "inbound",
            "label": "ORBION Inbound",
            "description": "Descarga de contenedores, tiempos, SLA e incidencias.",
            "url": "/inbound",    # home del módulo inbound
        },
        {
            "slug": "analytics",
            "label": "Analytics & IA Operacional",
            "description": "Métricas avanzadas, modelos y proyecciones.",
            "url": "#",           # futuro
        },
    ]

    dashboard_modules: list[dict] = []
    show_superadmin_console = False

    # ============================
    # 1) SUPERADMIN GLOBAL
    # ============================
    if rol_real == "superadmin":
        for m in base_modules:
            dashboard_modules.append(
                {
                    **m,
                    "locked": False,
                    "badge": "Activo",
                }
            )
        show_superadmin_console = True

    # ============================
    # 2) ADMIN DE NEGOCIO
    # ============================
    elif rol_efectivo == "admin" and negocio is not None:
        activos: set[str] = set()

        # Core WMS según plan
        if negocio_tiene_core_wms(negocio):
            activos.add("core_wms")

        # Inbound según plan
        if negocio_tiene_inbound(negocio):
            activos.add("inbound")

            # Analytics inbound / IA según plan inbound
            inbound_cfg = get_inbound_plan_config(negocio.plan_tipo)
            if inbound_cfg.get("enable_inbound_analytics"):
                activos.add("analytics")

        # Construir módulos marcando locked / activo
        for m in base_modules:
            if m["slug"] in activos:
                dashboard_modules.append(
                    {
                        **m,
                        "locked": False,
                        "badge": "Activo",
                    }
                )
            else:
                dashboard_modules.append(
                    {
                        **m,
                        "locked": True,
                        "badge": "Mejora tu plan",
                    }
                )

    # ============================
    # 3) OPERADOR
    # ============================
    else:
        # Operador ve solo los módulos habilitados por plan del negocio
        if negocio is not None:
            if negocio_tiene_core_wms(negocio):
                for m in base_modules:
                    if m["slug"] == "core_wms":
                        dashboard_modules.append(
                            {
                                **m,
                                "locked": False,
                                "badge": "Activo",
                            }
                        )

            if negocio_tiene_inbound(negocio):
                for m in base_modules:
                    if m["slug"] == "inbound":
                        dashboard_modules.append(
                            {
                                **m,
                                "locked": False,
                                "badge": "Activo",
                            }
                        )
        # Si por algún motivo no hay negocio asociado, no mostramos módulos
        show_superadmin_console = False

    return templates.TemplateResponse(
        "app/orbion_hub.html",
        {
            "request": request,
            "user": user,
            "dashboard_modules": dashboard_modules,
            "show_superadmin_console": show_superadmin_console,
        },
    )
