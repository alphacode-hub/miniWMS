# core/routes/routes_app_hub.py
"""
ORBION App Hub – SaaS enterprise module launcher

✔ Hub central de módulos (fuente de verdad: entitlements snapshot)
✔ Soporte superadmin (global + impersonado)
✔ Control por rol + suscripción por módulo
✔ Flags locked / activo / estados para UI
✔ Preparado para billing, addons y analytics
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.web import templates
from core.database import get_db
from core.security import require_user_dep
from core.models import Negocio

from core.models.enums import ModuleKey
from core.services.services_entitlements import get_entitlements_snapshot
from core.logging_config import logger
from core.services.services_subscriptions import (
    activate_module,
    cancel_subscription_at_period_end,
    unschedule_cancel
)
from core.models.saas import SuscripcionModulo



# ============================
# ROUTER
# ============================

router = APIRouter(
    prefix="/app",
    tags=["app-hub"],
)


# ============================
# HELPERS UI (solo Hub)
# ============================

def _badge_from_status(status: str, enabled: bool) -> str:
    if not enabled:
        # no habilitado por suscripción o estado
        if status in ("past_due", "suspended"):
            return "Pago pendiente"
        if status == "cancelled":
            return "Cancelado"
        return "No activo"
    # enabled
    if status == "trial":
        return "Trial"
    if status == "active":
        return "Activo"
    return status


def _best_metric_summary(module_slug: str, mod: dict) -> tuple[str | None, float | None, float | None]:
    """
    Retorna (label, used, limit) con la métrica más representativa del módulo.
    Esto alimenta la UI del hub (sin acoplarse a módulos internos).
    """
    limits = mod.get("limits") or {}
    usage = mod.get("usage") or {}

    # preferencia por módulo
    if module_slug == "inbound":
        key = "recepciones_mes"
        if key in limits:
            return ("Recepciones", float(usage.get(key, 0.0)), float(limits.get(key, 0.0)))
        key = "incidencias_mes"
        if key in limits:
            return ("Incidencias", float(usage.get(key, 0.0)), float(limits.get(key, 0.0)))

    if module_slug == "wms":
        key = "movimientos_mes"
        if key in limits:
            return ("Movimientos", float(usage.get(key, 0.0)), float(limits.get(key, 0.0)))
        key = "productos"
        if key in limits:
            return ("Productos", float(usage.get(key, 0.0)), float(limits.get(key, 0.0)))

    return (None, None, None)


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
    - Admin: ve módulos + locked/estado según suscripción
    - Operador: ve solo módulos habilitados
    """

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")

    negocio: Negocio | None = None
    if negocio_id:
        negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()

    # ============================
    # CATALOGO DE MÓDULOS (UI)
    # ============================
    # Nota: El catálogo es UI/marketing; la verdad de habilitación viene de entitlements.
    base_modules: list[dict] = [
        {
            "slug": "wms",
            "label": "ORBION Core WMS",
            "description": "Inventario, ubicaciones, rotación y auditoría.",
            "url": "/dashboard",
            "module_key": ModuleKey.WMS,
        },
        {
            "slug": "inbound",
            "label": "ORBION Inbound",
            "description": "Recepciones, pallets, checklist, incidencias, evidencia y analytics.",
            "url": "/inbound",
            "module_key": ModuleKey.INBOUND,
        },
        {
            "slug": "analytics_plus",
            "label": "Analytics & IA Operacional",
            "description": "Cross-módulo, modelos predictivos y proyecciones (futuro).",
            "url": "#",
            "module_key": None,  # no disponible aún
        },
    ]

    dashboard_modules: list[dict] = []
    show_superadmin_console: bool = False

    # ============================
    # SUPERADMIN GLOBAL (no impersonando)
    # ============================

    if rol_real == "superadmin" and not user.get("impersonando_negocio_id"):
        for m in base_modules:
            dashboard_modules.append(
                {
                    **m,
                    "locked": False,
                    "enabled": True,
                    "badge": "Superadmin",
                    "status": "superadmin",
                    "segmento": None,
                    "metric_label": None,
                    "metric_used": None,
                    "metric_limit": None,
                    "period_end": None,
                }
            )

        show_superadmin_console = True
        logger.info("[HUB] superadmin_global email=%s", user.get("email"))

        return templates.TemplateResponse(
            "app/orbion_hub.html",
            {
                "request": request,
                "user": user,
                "negocio": negocio,
                "dashboard_modules": dashboard_modules,
                "show_superadmin_console": show_superadmin_console,
                # para la UI SaaS
                "entitlements": None,
                "needs_onboarding": False,
            },
        )

    # ============================
    # NEGOCIO REQUERIDO PARA HUB SaaS
    # ============================

    if negocio is None:
        # Caso raro: usuario sin negocio_id (no superadmin global)
        logger.warning("[HUB] usuario sin negocio. email=%s rol=%s", user.get("email"), rol_efectivo)
        return templates.TemplateResponse(
            "app/orbion_hub.html",
            {
                "request": request,
                "user": user,
                "negocio": None,
                "dashboard_modules": [],
                "show_superadmin_console": False,
                "entitlements": None,
                "needs_onboarding": True,
            },
        )

    # ============================
    # SNAPSHOT ENTITLEMENTS (fuente única)
    # ============================

    ent = get_entitlements_snapshot(db, negocio.id)
    ent_modules = ent.get("modules", {}) or {}
    segmento = (ent.get("negocio", {}) or {}).get("segmento")

    # needs_onboarding: negocio sin módulos contratados
    # (Si no existen suscripciones aún, ent_modules viene vacío)
    needs_onboarding = len(ent_modules.keys()) == 0

    # ============================
    # ADMIN (o superadmin impersonando)
    # ============================

    if rol_efectivo == "admin":
        for m in base_modules:
            mk = m.get("module_key")
            # módulos futuros sin module_key aún
            if mk is None:
                dashboard_modules.append(
                    {
                        **m,
                        "locked": True,
                        "enabled": False,
                        "badge": "Próximamente",
                        "status": "coming_soon",
                        "segmento": segmento,
                        "metric_label": None,
                        "metric_used": None,
                        "metric_limit": None,
                        "period_end": None,
                    }
                )
                continue

            mod = ent_modules.get(mk.value)  # keys: "inbound", "wms"
            enabled = bool(mod and mod.get("enabled"))
            status = (mod or {}).get("status") or "inactive"
            badge = _badge_from_status(status, enabled)

            metric_label, metric_used, metric_limit = _best_metric_summary(m["slug"], mod or {})
            period_end = (mod or {}).get("period", {}).get("end")

            dashboard_modules.append(
                {
                    **m,
                    "locked": not enabled,
                    "enabled": enabled,
                    "badge": badge if enabled else "Activar módulo",
                    "status": status,
                    "segmento": segmento,
                    "metric_label": metric_label,
                    "metric_used": metric_used,
                    "metric_limit": metric_limit,
                    "period_end": period_end,
                }
            )

        logger.info(
            "[HUB] admin email=%s negocio_id=%s segmento=%s",
            user.get("email"),
            negocio.id,
            segmento,
        )

    # ============================
    # OPERADOR / OTROS ROLES
    # ============================

    else:
        # Operador solo ve módulos habilitados (enabled=True)
        for m in base_modules:
            mk = m.get("module_key")
            if mk is None:
                continue

            mod = ent_modules.get(mk.value)
            enabled = bool(mod and mod.get("enabled"))
            if not enabled:
                continue

            status = (mod or {}).get("status") or "inactive"
            badge = _badge_from_status(status, enabled)

            metric_label, metric_used, metric_limit = _best_metric_summary(m["slug"], mod or {})
            period_end = (mod or {}).get("period", {}).get("end")

            dashboard_modules.append(
                {
                    **m,
                    "locked": False,
                    "enabled": True,
                    "badge": badge,
                    "status": status,
                    "segmento": segmento,
                    "metric_label": metric_label,
                    "metric_used": metric_used,
                    "metric_limit": metric_limit,
                    "period_end": period_end,
                }
            )

        logger.info(
            "[HUB] operador email=%s negocio_id=%s segmento=%s",
            user.get("email"),
            negocio.id,
            segmento,
        )

    return templates.TemplateResponse(
        "app/orbion_hub.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "dashboard_modules": dashboard_modules,
            "show_superadmin_console": show_superadmin_console,
            # SaaS snapshot para UI (si quieres usarlo directo en template)
            "entitlements": ent,
            "needs_onboarding": needs_onboarding,
        },
    )

# ============================
# ACTIVATE MODULE (POST)
# ============================

@router.post("/modules/{module_key}/activate")
async def activate_module_from_hub(
    module_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    """
    Activa un módulo para el negocio actual (trial por defecto).

    Reglas:
    - Solo admin (o superadmin impersonando)
    - El módulo debe existir
    - Redirige al Hub
    """

    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")

    if rol_efectivo != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")

    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    # Validar módulo
    try:
        mk = ModuleKey(module_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="Módulo no válido")

    # Activar módulo (trial por defecto)
    sub = activate_module(
        db,
        negocio_id=negocio_id,
        module_key=mk,
        start_trial=True,
    )

    db.commit()

    logger.info(
        "[HUB] modulo_activado email=%s negocio_id=%s modulo=%s status=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        sub.status.value,
    )

    # Redirigir al hub
    return RedirectResponse(
        url="/app",
        status_code=303,
    )


# ============================
# CANCEL MODULE (POST)
# ============================

@router.post("/modules/{module_key}/cancel")
async def cancel_module_from_hub(
    module_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    """
    Cancela un módulo para el negocio actual al final del período.

    Reglas:
    - Solo admin (o superadmin impersonando)
    - El módulo debe existir
    - Debe existir suscripción para cancelar
    - Cancela al fin del período (cancel_at_period_end=1)
    - Redirige al Hub
    """

    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")

    if rol_efectivo != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")

    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    # Validar módulo
    try:
        mk = ModuleKey(module_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="Módulo no válido")

    # Buscar suscripción
    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )

    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo")

    # Cancelar al fin del período
    cancel_subscription_at_period_end(db, sub)
    db.commit()

    logger.info(
        "[HUB] modulo_cancelado email=%s negocio_id=%s modulo=%s cancel_at_period_end=1 status=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        sub.status.value,
    )

    return RedirectResponse(
        url="/app",
        status_code=303,
    )

@router.post("/modules/{module_key}/reactivate")
async def reactivate_module_from_hub(
    module_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    rol = user.get("rol")
    negocio_id = user.get("negocio_id")

    if rol != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")
    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    try:
        mk = ModuleKey(module_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="Módulo no válido")

    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo")

    unschedule_cancel(db, sub)
    db.commit()

    logger.info(
        "[HUB] modulo_reactivado email=%s negocio_id=%s modulo=%s",
        user.get("email"), negocio_id, mk.value
    )

    return RedirectResponse(url="/app", status_code=303)
