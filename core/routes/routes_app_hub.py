# core/routes/routes_app_hub.py
"""
ORBION App Hub – SaaS enterprise module launcher (baseline aligned)

✔ Hub central de módulos (fuente de verdad: entitlements snapshot)
✔ Soporte superadmin (global + impersonado)
✔ Control por rol + suscripción por módulo
✔ Flags locked / activo / estados para UI
✔ Preparado para billing, addons y analytics

Baseline:
- Superadmin global (NO impersonando) NO usa el Hub -> redirect a /superadmin/dashboard
- Impersonación oficial: acting_negocio_id (cookie payload)
- Snapshot shape:
    snapshot = { negocio:{...segment}, entitlements:{...}, modules:{...} }
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

router = APIRouter(prefix="/app", tags=["app-hub"])


# =========================================================
# HELPERS UI (solo Hub)
# =========================================================

_ALLOWED_ACCESS_STATUSES = {"trial", "active"}


def _norm_status(status: str) -> str:
    return (status or "").strip().lower() or "inactive"


def _is_access_allowed(enabled: bool, status: str) -> bool:
    """
    Contrato enterprise:
    - enabled es “provisionado / habilitado”
    - acceso real SOLO si status en {trial, active}
    """
    st = _norm_status(status)
    return bool(enabled) and (st in _ALLOWED_ACCESS_STATUSES)


def _badge_from_status(status: str, enabled: bool) -> str:
    """
    Badge humano para el Hub.
    Nota: NO confundir enabled con acceso.
    """
    st = _norm_status(status)

    # Si no está provisionado
    if not enabled:
        if st in ("past_due", "suspended"):
            return "Pago pendiente"
        if st == "cancelled":
            return "Cancelado"
        return "No activo"

    # Está provisionado (enabled=True), ahora mirar estado de acceso
    if st == "trial":
        return "Trial"
    if st == "active":
        return "Activo"
    if st in ("past_due", "suspended"):
        return "Suspendido"
    if st == "cancelled":
        return "Cancelado"
    if st == "inactive":
        return "Inactivo"

    return st


def _period_end_from_mod(mod: dict) -> str | None:
    """
    Preferimos overlay de suscripción:
      mod.subscription.period.end
    Si no, fallback:
      mod.period.to / mod.period.end
    """
    sub = mod.get("subscription") or {}
    if isinstance(sub, dict):
        per = sub.get("period") or {}
        if isinstance(per, dict):
            end = per.get("end")
            if end:
                return str(end)

    per2 = mod.get("period") or {}
    if isinstance(per2, dict):
        end2 = per2.get("to") or per2.get("end")
        if end2:
            return str(end2)

    return None


def _best_metric_summary(module_slug: str, mod: dict) -> tuple[str | None, int | None, int | None]:
    """
    Muestra métricas "de conteo" en formato entero (sin decimales).
    Soporta snapshots viejos y nuevos:
    - limits: dict
    - usage: dict (si existe)
    - remaining: dict (si existe)  -> used = limit - remaining
    """
    limits = mod.get("limits") if isinstance(mod.get("limits"), dict) else {}
    usage = mod.get("usage") if isinstance(mod.get("usage"), dict) else {}
    remaining = mod.get("remaining") if isinstance(mod.get("remaining"), dict) else {}

    def _to_int(x) -> int:
        try:
            return int(float(x))
        except Exception:
            return 0

    def _get_used_limit_int(key: str) -> tuple[int | None, int | None]:
        if key not in limits:
            return (None, None)

        lim = _to_int(limits.get(key, 0))
        used: int | None = None

        if key in usage:
            try:
                used = _to_int(usage.get(key, 0))
            except Exception:
                used = None

        if used is None and key in remaining:
            try:
                rem = _to_int(remaining.get(key, 0))
                used = max(lim - rem, 0)
            except Exception:
                used = None

        if used is None:
            used = 0

        return (used, lim)

    if module_slug == "inbound":
        for key, label in (("recepciones_mes", "Recepciones"), ("incidencias_mes", "Incidencias")):
            if key in limits:
                used, lim = _get_used_limit_int(key)
                return (label, used, lim)

    if module_slug == "wms":
        for key, label in (("movimientos_mes", "Movimientos"), ("productos", "Productos")):
            if key in limits:
                used, lim = _get_used_limit_int(key)
                return (label, used, lim)

    return (None, None, None)


def _compute_needs_onboarding(snapshot_modules: dict) -> bool:
    """
    Onboarding real enterprise:
    - Ignora "core"
    - Si no hay módulos operativos con ACCESS_ALLOWED -> True
    """
    if not isinstance(snapshot_modules, dict) or not snapshot_modules:
        return True

    for slug, mod in snapshot_modules.items():
        if slug == "core":
            continue
        if not isinstance(mod, dict):
            continue

        enabled = bool(mod.get("enabled"))
        st = _norm_status(mod.get("status"))
        if _is_access_allowed(enabled, st):
            return False

    return True


def _mk_from_str(module_key: str) -> ModuleKey:
    s = (module_key or "").strip().lower()
    if not s:
        raise HTTPException(status_code=404, detail="Módulo no válido")
    try:
        return ModuleKey(s)
    except Exception:
        raise HTTPException(status_code=404, detail="Módulo no válido")


def _effective_negocio_id(user: dict) -> int | None:
    """
    Regla baseline para contexto negocio:
    - Si hay acting_negocio_id (impersonación) -> usarlo
    - Si no, usar negocio_id propio
    """
    try:
        if user.get("acting_negocio_id"):
            return int(user["acting_negocio_id"])
    except Exception:
        pass

    try:
        if user.get("negocio_id"):
            return int(user["negocio_id"])
    except Exception:
        pass

    return None


# =========================================================
# HUB VIEW
# =========================================================

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
    - Superadmin impersonando (acting_negocio_id): usa Hub como admin
    - Admin: ve módulos + locked/estado según snapshot (ACCESS_ALLOWED)
    - Operador: ve solo módulos con ACCESS_ALLOWED
    """

    rol_real = (user.get("rol_real") or user.get("rol") or "").strip().lower()
    rol_efectivo = (user.get("rol") or "").strip().lower()

    acting_id = user.get("acting_negocio_id")
    is_superadmin_global = (rol_real == "superadmin") and (not acting_id)

    if is_superadmin_global:
        logger.info("[HUB] superadmin_global -> redirect /superadmin/dashboard email=%s", user.get("email"))
        return RedirectResponse(url="/superadmin/dashboard", status_code=302)

    negocio_id = _effective_negocio_id(user)
    if not negocio_id:
        logger.warning("[HUB] usuario sin negocio_id efectivo. email=%s rol=%s", user.get("email"), rol_efectivo)
        return templates.TemplateResponse(
            "app/orbion_hub.html",
            {
                "request": request,
                "user": user,
                "negocio": None,
                "dashboard_modules": [],
                "show_superadmin_console": False,
                "snapshot": None,
                "entitlements": None,
                "negocio_ctx": None,
                "needs_onboarding": True,
            },
        )

    negocio: Negocio | None = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        logger.warning("[HUB] negocio no encontrado. negocio_id=%s email=%s", negocio_id, user.get("email"))
        return templates.TemplateResponse(
            "app/orbion_hub.html",
            {
                "request": request,
                "user": user,
                "negocio": None,
                "dashboard_modules": [],
                "show_superadmin_console": False,
                "snapshot": None,
                "entitlements": None,
                "negocio_ctx": None,
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
            "module_key": None,  # coming_soon
        },
    ]

    # Snapshot (fuente única)
    snapshot = get_entitlements_snapshot(db, negocio.id)
    snapshot_modules = (snapshot.get("modules") or {}) if isinstance(snapshot, dict) else {}
    entitlements = (snapshot.get("entitlements") or {}) if isinstance(snapshot, dict) else {}

    # ✅ negocio_ctx viene desde snapshot["negocio"], NO desde entitlements
    negocio_ctx = (snapshot.get("negocio") or {}) if isinstance(snapshot, dict) else {}

    segmento = (
        (negocio_ctx.get("segment") if isinstance(negocio_ctx, dict) else None)
        or entitlements.get("segment")
        or "emprendedor"
    )
    segmento = str(segmento).strip().lower() or "emprendedor"

    needs_onboarding = _compute_needs_onboarding(snapshot_modules)

    dashboard_modules: list[dict] = []
    show_superadmin_console = False  # baseline: superadmin global no usa Hub

    # Admin: ve todo (con locked / activación)
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
                        "cancel_at_period_end": False,
                        "trial_ends_at": None,
                    }
                )
                continue

            mod = snapshot_modules.get(mk.value) or {}
            enabled = bool(mod.get("enabled"))
            status = _norm_status(mod.get("status") or "inactive")

            access_allowed = _is_access_allowed(enabled, status)
            badge = _badge_from_status(status, enabled)

            metric_label, metric_used, metric_limit = _best_metric_summary(m["slug"], mod)
            period_end = _period_end_from_mod(mod)

            sub = mod.get("subscription") or {}
            cancel_at_period_end = bool(sub.get("cancel_at_period_end")) if isinstance(sub, dict) else False
            trial_ends_at = sub.get("trial_ends_at") if isinstance(sub, dict) else None

            dashboard_modules.append(
                {
                    **m,
                    # ✅ locked si NO hay acceso (aunque enabled sea True)
                    "locked": (not access_allowed),
                    # ✅ enabled se mantiene como “provisionado”
                    "enabled": enabled,
                    # ✅ badge coherente
                    "badge": badge if enabled else "Activar módulo",
                    "status": status,
                    "segmento": segmento,
                    "metric_label": metric_label,
                    "metric_used": metric_used,
                    "metric_limit": metric_limit,
                    "period_end": period_end,
                    "cancel_at_period_end": cancel_at_period_end,
                    "trial_ends_at": str(trial_ends_at) if trial_ends_at else None,
                    # ✅ extra útil para depurar (si quieres mostrarlo luego)
                    "access_allowed": access_allowed,
                }
            )

        logger.info(
            "[HUB] admin email=%s negocio_id=%s segmento=%s acting=%s",
            user.get("email"),
            negocio.id,
            segmento,
            bool(user.get("acting_negocio_id")),
        )

    # Operador / otros: SOLO acceso real (trial/active)
    else:
        for m in base_modules:
            mk = m.get("module_key")
            if mk is None:
                continue

            mod = snapshot_modules.get(mk.value) or {}
            enabled = bool(mod.get("enabled"))
            status = _norm_status(mod.get("status") or "inactive")

            if not _is_access_allowed(enabled, status):
                continue

            badge = _badge_from_status(status, enabled)
            metric_label, metric_used, metric_limit = _best_metric_summary(m["slug"], mod)
            period_end = _period_end_from_mod(mod)

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
                    "cancel_at_period_end": False,
                    "trial_ends_at": None,
                    "access_allowed": True,
                }
            )

        logger.info(
            "[HUB] operador email=%s negocio_id=%s segmento=%s acting=%s",
            user.get("email"),
            negocio.id,
            segmento,
            bool(user.get("acting_negocio_id")),
        )

    return templates.TemplateResponse(
        "app/orbion_hub.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "dashboard_modules": dashboard_modules,
            "show_superadmin_console": show_superadmin_console,
            "snapshot": snapshot,
            "entitlements": entitlements,
            "negocio_ctx": negocio_ctx,
            "needs_onboarding": needs_onboarding,
        },
    )


# =========================================================
# ACTIONS DESDE HUB (ADMIN)
# =========================================================

@router.post("/modules/{module_key}/activate")
async def activate_module_from_hub(
    module_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    rol = (user.get("rol") or "").strip().lower()
    negocio_id = _effective_negocio_id(user)

    if rol != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")
    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    mk = _mk_from_str(module_key)

    before_status: str | None = None
    try:
        sub_before = (
            db.query(SuscripcionModulo)
            .filter(SuscripcionModulo.negocio_id == negocio_id)
            .filter(SuscripcionModulo.module_key == mk)
            .first()
        )
        if sub_before and getattr(sub_before, "status", None) is not None:
            before_status = getattr(sub_before.status, "value", str(sub_before.status))
    except Exception:
        pass

    sub = activate_module(db, negocio_id=negocio_id, module_key=mk, start_trial=True)
    db.commit()

    logger.info(
        "[HUB] module_activate email=%s negocio_id=%s module=%s before=%s after=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        before_status,
        getattr(sub.status, "value", str(sub.status)),
    )

    return RedirectResponse(url="/app", status_code=303)


@router.post("/modules/{module_key}/cancel")
async def cancel_module_from_hub(
    module_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    rol = (user.get("rol") or "").strip().lower()
    negocio_id = _effective_negocio_id(user)

    if rol != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")
    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    mk = _mk_from_str(module_key)

    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo")

    before_status = getattr(sub.status, "value", str(sub.status)) if getattr(sub, "status", None) else None
    before_cancel = bool(getattr(sub, "cancel_at_period_end", 0))

    cancel_subscription_at_period_end(db, sub)
    db.commit()

    logger.info(
        "[HUB] module_cancel email=%s negocio_id=%s module=%s before_status=%s before_cancel=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        before_status,
        before_cancel,
    )

    return RedirectResponse(url="/app", status_code=303)


@router.post("/modules/{module_key}/reactivate")
async def reactivate_module_from_hub(
    module_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    rol = (user.get("rol") or "").strip().lower()
    negocio_id = _effective_negocio_id(user)

    if rol != "admin":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")
    if not negocio_id:
        raise HTTPException(status_code=400, detail="Usuario sin negocio asociado")

    mk = _mk_from_str(module_key)

    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo")

    before_cancel = bool(getattr(sub, "cancel_at_period_end", 0))

    unschedule_cancel(db, sub)
    db.commit()

    logger.info(
        "[HUB] module_reactivate email=%s negocio_id=%s module=%s before_cancel=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        before_cancel,
    )

    return RedirectResponse(url="/app", status_code=303)
