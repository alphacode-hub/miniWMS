"""
ORBION App Hub – SaaS enterprise module launcher (baseline aligned)

✔ Hub central de módulos (fuente de verdad: entitlements snapshot)
✔ Soporte superadmin (global + impersonado)
✔ Control por rol + suscripción por módulo
✔ coming_soon: bloquea CTAs y acciones (no vendible aún)

Baseline:
- Superadmin global (NO impersonando) NO usa el Hub -> redirect a /superadmin/dashboard
- Impersonación oficial: acting_negocio_id (cookie payload)
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, HTTPException, Form
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
    mark_paid_now,
)
from core.models.saas import SuscripcionModulo

router = APIRouter(prefix="/app", tags=["app-hub"])

_ALLOWED_ACCESS_STATUSES = {"trial", "active"}


def _norm_status(status: str) -> str:
    return (status or "").strip().lower() or "inactive"


def _is_access_allowed(enabled: bool, status: str, coming_soon: bool) -> bool:
    if coming_soon:
        return False
    st = _norm_status(status)
    return bool(enabled) and (st in _ALLOWED_ACCESS_STATUSES)


def _badge_from_status(status: str, enabled: bool, coming_soon: bool) -> str:
    if coming_soon:
        return "Próximamente"

    st = _norm_status(status)

    if not enabled:
        if st in ("past_due", "suspended"):
            return "Pago pendiente"
        if st == "cancelled":
            return "Cancelado"
        return "No activo"

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


def _period_end_from_mod(mod: dict, coming_soon: bool) -> str | None:
    if coming_soon:
        return None

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


def _best_metric_summary(module_slug: str, mod: dict, coming_soon: bool) -> tuple[str | None, int | None, int | None]:
    if coming_soon:
        return (None, None, None)

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
            used = _to_int(usage.get(key, 0))

        if used is None and key in remaining:
            rem = _to_int(remaining.get(key, 0))
            used = max(lim - rem, 0)

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
    if not isinstance(snapshot_modules, dict) or not snapshot_modules:
        return True

    for slug, mod in snapshot_modules.items():
        if slug == "core":
            continue
        if not isinstance(mod, dict):
            continue

        enabled = bool(mod.get("enabled"))
        st = _norm_status(mod.get("status"))
        coming_soon = bool(mod.get("coming_soon", False))

        if _is_access_allowed(enabled, st, coming_soon):
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


def _is_coming_soon_for(db: Session, negocio_id: int, mk: ModuleKey) -> bool:
    snap = get_entitlements_snapshot(db, negocio_id)
    mods = (snap.get("modules") or {})
    mod = mods.get(mk.value) if isinstance(mods, dict) else {}
    return bool(mod.get("coming_soon", False)) if isinstance(mod, dict) else False


@router.get("", response_class=HTMLResponse)
async def orbion_hub_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
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
                "snapshot": None,
                "entitlements": None,
                "negocio_ctx": None,
                "needs_onboarding": True,
            },
        )

    # 🔥 Importante: aquí “core” NO es un módulo del Hub.
    base_modules: list[dict] = [
        {
            "slug": "inbound",
            "label": "ORBION Inbound",
            "description": "Recepciones, pallets, checklist, incidencias, evidencia y analytics.",
            "url": "/inbound",
            "module_key": ModuleKey.INBOUND,
        },
        {
            "slug": "wms",
            "label": "ORBION Core WMS",
            "description": "Inventario, ubicaciones, rotación y auditoría.",
            "url": "/dashboard",
            "module_key": ModuleKey.WMS,
        },
        {
            "slug": "analytics_plus",
            "label": "Analytics Plus",
            "description": "Analítica avanzada cross-módulo (futuro).",
            "url": "#",
            "module_key": None,
        },
        {
            "slug": "ml_ia",
            "label": "ML/IA Operacional",
            "description": "Modelos predictivos, scoring y automatizaciones (futuro).",
            "url": "#",
            "module_key": None,
        },
    ]

    snapshot = get_entitlements_snapshot(db, negocio.id)
    snapshot_modules = (snapshot.get("modules") or {}) if isinstance(snapshot, dict) else {}
    entitlements = (snapshot.get("entitlements") or {}) if isinstance(snapshot, dict) else {}
    negocio_ctx = (snapshot.get("negocio") or {}) if isinstance(snapshot, dict) else {}

    segmento = (
        (negocio_ctx.get("segment") if isinstance(negocio_ctx, dict) else None)
        or entitlements.get("segment")
        or "emprendedor"
    )
    segmento = str(segmento).strip().lower() or "emprendedor"

    needs_onboarding = _compute_needs_onboarding(snapshot_modules)

    dashboard_modules: list[dict] = []

    if rol_efectivo == "admin":
        for m in base_modules:
            mk = m.get("module_key")

            # módulos “futuros” sin mk (solo UI)
            if mk is None:
                dashboard_modules.append(
                    {
                        **m,
                        "locked": True,
                        "enabled": False,
                        "badge": "Próximamente",
                        "status": "coming_soon",
                        "coming_soon": True,
                        "segmento": segmento,
                        "metric_label": None,
                        "metric_used": None,
                        "metric_limit": None,
                        "period_end": None,
                        "cancel_at_period_end": False,
                        "trial_ends_at": None,
                        "access_allowed": False,
                    }
                )
                continue

            mod = snapshot_modules.get(mk.value) or {}
            mod = mod if isinstance(mod, dict) else {}

            coming_soon = bool(mod.get("coming_soon", False))
            enabled = bool(mod.get("enabled"))
            status = _norm_status(mod.get("status") or "inactive")

            access_allowed = _is_access_allowed(enabled, status, coming_soon)
            badge = _badge_from_status(status, enabled, coming_soon)

            metric_label, metric_used, metric_limit = _best_metric_summary(m["slug"], mod, coming_soon)
            period_end = _period_end_from_mod(mod, coming_soon)

            sub = mod.get("subscription") or {}
            cancel_at_period_end = bool(sub.get("cancel_at_period_end")) if isinstance(sub, dict) else False
            trial_ends_at = sub.get("trial_ends_at") if isinstance(sub, dict) else None

            # ✅ si coming_soon, NO queremos CTA “Activar módulo”
            if coming_soon:
                enabled = False
                access_allowed = False

            dashboard_modules.append(
                {
                    **m,
                    "locked": (not access_allowed),
                    "enabled": enabled,
                    "badge": badge,
                    "status": ("coming_soon" if coming_soon else status),
                    "coming_soon": coming_soon,
                    "segmento": segmento,
                    "metric_label": metric_label,
                    "metric_used": metric_used,
                    "metric_limit": metric_limit,
                    "period_end": period_end,
                    "cancel_at_period_end": cancel_at_period_end if (not coming_soon) else False,
                    "trial_ends_at": str(trial_ends_at) if (trial_ends_at and not coming_soon) else None,
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

    else:
        # operador: solo módulos con acceso real (y no coming_soon)
        for m in base_modules:
            mk = m.get("module_key")
            if mk is None:
                continue

            mod = snapshot_modules.get(mk.value) or {}
            mod = mod if isinstance(mod, dict) else {}

            coming_soon = bool(mod.get("coming_soon", False))
            enabled = bool(mod.get("enabled"))
            status = _norm_status(mod.get("status") or "inactive")

            if not _is_access_allowed(enabled, status, coming_soon):
                continue

            badge = _badge_from_status(status, enabled, coming_soon)
            metric_label, metric_used, metric_limit = _best_metric_summary(m["slug"], mod, coming_soon)
            period_end = _period_end_from_mod(mod, coming_soon)

            dashboard_modules.append(
                {
                    **m,
                    "locked": False,
                    "enabled": True,
                    "badge": badge,
                    "status": status,
                    "coming_soon": False,
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
            "snapshot": snapshot,
            "entitlements": entitlements,
            "negocio_ctx": negocio_ctx,
            "needs_onboarding": needs_onboarding,
        },
    )


# =========================================================
# ACTIONS (ADMIN) - BLOQUEAR coming_soon
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

    # ✅ no activable si coming_soon
    if _is_coming_soon_for(db, negocio_id, mk):
        return RedirectResponse(url="/app/planes?error=Módulo próximamente (no disponible aún)", status_code=303)

    sub_before_status: str | None = None
    try:
        sub_before = (
            db.query(SuscripcionModulo)
            .filter(SuscripcionModulo.negocio_id == negocio_id)
            .filter(SuscripcionModulo.module_key == mk)
            .first()
        )
        if sub_before and getattr(sub_before, "status", None) is not None:
            sub_before_status = getattr(sub_before.status, "value", str(sub_before.status))
    except Exception:
        pass

    sub = activate_module(db, negocio_id=negocio_id, module_key=mk, start_trial=True, actor=user)
    db.commit()

    logger.info(
        "[HUB] module_activate email=%s negocio_id=%s module=%s before=%s after=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        sub_before_status,
        getattr(sub.status, "value", str(sub.status)),
    )

    return RedirectResponse(url="/app/planes?ok=Trial activado", status_code=303)


@router.post("/modules/{module_key}/pay_now")
async def pay_now_module_from_hub(
    module_key: str,
    request: Request,
    months: int = Form(1),
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

    if _is_coming_soon_for(db, negocio_id, mk):
        return RedirectResponse(url="/app/planes?error=Módulo próximamente (no disponible aún)", status_code=303)

    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )

    if not sub:
        sub = activate_module(db, negocio_id=negocio_id, module_key=mk, start_trial=False, actor=user)
    else:
        mark_paid_now(db, sub, months=max(1, int(months or 1)), actor=user)

    db.commit()

    logger.info(
        "[HUB] module_pay_now email=%s negocio_id=%s module=%s months=%s status=%s",
        user.get("email"),
        negocio_id,
        mk.value,
        months,
        getattr(sub.status, "value", str(sub.status)),
    )

    return RedirectResponse(url="/app/planes?ok=Pago simulado: módulo activado", status_code=303)


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

    if _is_coming_soon_for(db, negocio_id, mk):
        return RedirectResponse(url="/app/planes?error=Módulo próximamente (no disponible aún)", status_code=303)

    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo")

    cancel_subscription_at_period_end(db, sub, actor=user)
    db.commit()

    return RedirectResponse(url="/app/planes?ok=Cancelación agendada", status_code=303)


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

    if _is_coming_soon_for(db, negocio_id, mk):
        return RedirectResponse(url="/app/planes?error=Módulo próximamente (no disponible aún)", status_code=303)

    sub: SuscripcionModulo | None = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo")

    unschedule_cancel(db, sub, actor=user)
    db.commit()

    return RedirectResponse(url="/app/planes?ok=Suscripción reactivada", status_code=303)
