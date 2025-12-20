# core/routes/routes_app_hub.py
"""
ORBION App Hub – SaaS enterprise module launcher

✔ Hub central de módulos (fuente de verdad: entitlements snapshot)
✔ Soporte superadmin (global + impersonado)
✔ Control por rol + suscripción por módulo
✔ Flags locked / activo / estados para UI
✔ Preparado para billing, addons y analytics

Cambio baseline:
- Superadmin global (no impersonando) NO usa el Hub -> redirect a /superadmin/dashboard
  (el Hub es para contexto-negocio).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, HTTPException
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
    unschedule_cancel,
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
        if status in ("past_due", "suspended"):
            return "Pago pendiente"
        if status == "cancelled":
            return "Cancelado"
        return "No activo"
    if status == "trial":
        return "Trial"
    if status == "active":
        return "Activo"
    return status


def _best_metric_summary(module_slug: str, mod: dict) -> tuple[str | None, float | None, float | None]:
    limits = mod.get("limits") or {}
    usage = mod.get("usage") or {}

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


def _compute_needs_onboarding(ent_modules: dict) -> bool:
    """
    Baseline: default_entitlements() siempre trae modules.core enabled.
    Onboarding debe reflejar "no hay módulos operativos contratados/activos (ej: WMS/Inbound)".
    """
    if not ent_modules:
        return True

    non_core = {k: v for k, v in ent_modules.items() if k != "core"}
    if not non_core:
        return True

    # Si ninguno de los módulos no-core está enabled, consideramos onboarding
    return not any(bool((m or {}).get("enabled")) for m in non_core.values())


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
    Hub central de módulos ORBION (contexto negocio).

    Reglas:
    - Superadmin global (NO impersonando): redirect a /superadmin/dashboard
    - Superadmin impersonando: se comporta como admin (usa Hub)
    - Admin: ve módulos + locked/estado según suscripción
    - Operador: ve solo módulos habilitados
    """

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")
    impersonando = bool(user.get("impersonando_negocio_id"))

    # ✅ Superadmin global NO usa Hub
    if rol_real == "superadmin" and not impersonando:
        logger.info("[HUB] superadmin_global -> redirect /superadmin/dashboard email=%s", user.get("email"))
        return RedirectResponse(url="/superadmin/dashboard", status_code=302)

    negocio: Negocio | None = None
    if negocio_id:
        negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()

    # Caso raro: usuario sin negocio_id (no superadmin global)
    if negocio is None:
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

    # Catálogo UI (marketing)
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
            "module_key": None,
        },
    ]

    dashboard_modules: list[dict] = []
    show_superadmin_console: bool = False  # ✅ ya no se usa para superadmin global aquí

    # Snapshot entitlements (fuente única)
    ent = get_entitlements_snapshot(db, negocio.id)
    ent_modules = ent.get("modules", {}) or {}
    segmento = (ent.get("negocio", {}) or {}).get("segmento")

    # ✅ Onboarding real (ignora core)
    needs_onboarding = _compute_needs_onboarding(ent_modules)

    # Admin (o superadmin impersonando -> rol_efectivo ya viene "admin")
    if rol_efectivo == "admin":
        for m in base_modules:
            mk = m.get("module_key")
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

            mod = ent_modules.get(mk.value)
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

        logger.info("[HUB] admin email=%s negocio_id=%s segmento=%s", user.get("email"), negocio.id, segmento)

    # Operador / otros roles: solo enabled
    else:
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

        logger.info("[HUB] operador email=%s negocio_id=%s segmento=%s", user.get("email"), negocio.id, segmento)

    return templates.TemplateResponse(
        "app/orbion_hub.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "dashboard_modules": dashboard_modules,
            "show_superadmin_console": show_superadmin_console,
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
    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")

    if rol_efectivo != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")
    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    try:
        mk = ModuleKey(module_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="Módulo no válido")

    sub = activate_module(db, negocio_id=negocio_id, module_key=mk, start_trial=True)
    db.commit()

    logger.info(
        "[HUB] modulo_activado email=%s negocio_id=%s modulo=%s status=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        sub.status.value,
    )

    return RedirectResponse(url="/app", status_code=303)


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
    rol_efectivo = user.get("rol")
    negocio_id = user.get("negocio_id")

    if rol_efectivo != "admin":
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

    cancel_subscription_at_period_end(db, sub)
    db.commit()

    logger.info(
        "[HUB] modulo_cancelado email=%s negocio_id=%s modulo=%s cancel_at_period_end=1 status=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        sub.status.value,
    )

    return RedirectResponse(url="/app", status_code=303)


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
        user.get("email"),
        negocio_id,
        mk.value,
    )

    return RedirectResponse(url="/app", status_code=303)
