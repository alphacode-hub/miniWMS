# core/routes/routes_app_hub.py
"""
ORBION App Hub – SaaS enterprise module launcher

✔ Hub central de módulos
✔ Soporte superadmin (global + impersonado)
✔ Control por plan y rol
✔ Flags locked / activo claros para UI
✔ Preparado para billing, addons y analytics
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from core.web import templates

from core.database import get_db
from core.security import require_user_dep
from core.models import Negocio
from core.services.services_modules import (
    negocio_tiene_core_wms,
    negocio_tiene_inbound,
)
from core.plans import get_inbound_plan_config
from core.logging_config import logger


# ============================
# ROUTER
# ============================

router = APIRouter(
    prefix="/app",
    tags=["app-hub"],
)


# ============================
# HUB VIEW
# ============================

@router.get("", response_class=HTMLResponse)
async def orbion_hub_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    """
    Hub central de módulos ORBION.

    Reglas:
    - Superadmin global: ve todo + consola
    - Superadmin impersonando: se comporta como admin
    - Admin: ve módulos según plan
    - Operador: ve solo módulos habilitados
    """

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")

    negocio: Negocio | None = None
    if negocio_id:
        negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()

    # ============================
    # DEFINICIÓN DE MÓDULOS
    # ============================

    base_modules: list[dict] = [
        {
            "slug": "core_wms",
            "label": "ORBION Core WMS",
            "description": "Inventario, ubicaciones, rotación y auditoría.",
            "url": "/dashboard",
        },
        {
            "slug": "inbound",
            "label": "ORBION Inbound",
            "description": "Descarga de contenedores, SLA, incidencias y control.",
            "url": "/inbound",
        },
        {
            "slug": "analytics",
            "label": "Analytics & IA Operacional",
            "description": "Métricas avanzadas, modelos predictivos y proyecciones.",
            "url": "#",  # futuro
        },
    ]

    dashboard_modules: list[dict] = []
    show_superadmin_console: bool = False

    # ============================
    # SUPERADMIN GLOBAL
    # ============================

    if rol_real == "superadmin" and not user.get("impersonando_negocio_id"):
        for m in base_modules:
            dashboard_modules.append(
                {
                    **m,
                    "locked": False,
                    "badge": "Activo",
                }
            )
        show_superadmin_console = True

        logger.info("[HUB] superadmin_global email=%s", user.get("email"))

    # ============================
    # ADMIN (o superadmin impersonando)
    # ============================

    elif rol_efectivo == "admin" and negocio is not None:
        activos: set[str] = set()

        if negocio_tiene_core_wms(negocio):
            activos.add("core_wms")

        if negocio_tiene_inbound(negocio):
            activos.add("inbound")

            inbound_cfg = get_inbound_plan_config(negocio.plan_tipo)
            if inbound_cfg.get("enable_inbound_analytics"):
                activos.add("analytics")

        for m in base_modules:
            dashboard_modules.append(
                {
                    **m,
                    "locked": m["slug"] not in activos,
                    "badge": "Activo" if m["slug"] in activos else "Mejora tu plan",
                }
            )

        logger.info(
            "[HUB] admin email=%s negocio_id=%s plan=%s",
            user.get("email"),
            negocio.id,
            negocio.plan_tipo,
        )

    # ============================
    # OPERADOR / OTROS ROLES
    # ============================

    else:
        if negocio is not None:
            if negocio_tiene_core_wms(negocio):
                dashboard_modules.append(
                    {
                        **base_modules[0],
                        "locked": False,
                        "badge": "Activo",
                    }
                )

            if negocio_tiene_inbound(negocio):
                dashboard_modules.append(
                    {
                        **base_modules[1],
                        "locked": False,
                        "badge": "Activo",
                    }
                )

        logger.info(
            "[HUB] operador email=%s negocio_id=%s",
            user.get("email"),
            negocio.id if negocio else None,
        )

    return templates.TemplateResponse(
        "app/orbion_hub.html",
        {
            "request": request,
            "user": user,
            "dashboard_modules": dashboard_modules,
            "show_superadmin_console": show_superadmin_console,
        },
    )
