# core/routes/routes_app_planes.py
"""
ORBION Plan Center – /app/planes (baseline aligned)

✅ Un solo lugar para:
- Ver segmento actual (snapshot)
- Ver módulos, estado, período
- Ver límites/uso (snapshot)
- Acciones básicas: activar / cancelar / reactivar (reusa endpoints del Hub)

Reglas enterprise:
- NO mostrar "core" (es del sistema, siempre ON)
- coming_soon => mostrar Próximamente y NO permitir acciones

✅ Extra enterprise:
- Para INBOUND.evidencias_mb: mostrar también "storage activo" (real) calculado desde storage
  (docs activos + fotos activas), sin romper el patrón de counters.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.web import templates
from core.database import get_db
from core.security import require_user_dep
from core.logging_config import logger
from core.services.services_entitlements import get_entitlements_snapshot
from core.services.services_module_counters import (
    build_counters_for_ui,
    build_inbound_counters_for_ui,
)

# ✅ NUEVO: métrica real (storage activo)
from core.services.services_inbound_storage_metrics import storage_activo_mb_inbound

router = APIRouter(prefix="/app", tags=["app-planes"])


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


def _as_int_if_whole(x):
    try:
        if x is None:
            return None
        f = float(x)
        if abs(f - round(f)) < 1e-9:
            return int(round(f))
        return f
    except Exception:
        return x


def _normalize_numeric_dict(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        out[k] = _as_int_if_whole(v)
    return out


def _norm_status(s: object) -> str:
    try:
        return str(s or "").strip().lower()
    except Exception:
        return "unknown"


def _normalize_subscription_for_ui(mod_status: str, sub: dict, ent_period: dict) -> tuple[str, dict, dict]:
    sub = sub or {}
    ent_period = ent_period or {}

    sub_status = _norm_status(sub.get("status"))
    status_final = sub_status if sub_status else _norm_status(mod_status)

    sub_period = sub.get("period") if isinstance(sub.get("period"), dict) else {}
    period_final = sub_period if sub_period else (ent_period if isinstance(ent_period, dict) else {})

    if status_final == "trial":
        if "period" in sub:
            sub.pop("period", None)
        period_final = {}
    else:
        sub.pop("trial_ends_at", None)

    return status_final, sub, period_final


def _counters_for_module(*, db: Session, negocio_id: int, slug: str, mod: dict) -> list[dict]:
    """
    Construye counters UI listos (sin cálculos en Jinja).
    Además:
      - inbound/evidencias_mb incluye storage activo real (docs+fotos activos).
    """
    if not isinstance(mod, dict):
        return []

    # Inbound: orden/labels oficiales
    if slug == "inbound":
        counters = build_inbound_counters_for_ui(mod)
    else:
        counters = build_counters_for_ui(mod)

    # ✅ storage activo (solo inbound)
    storage_activo_mb: float | None = None
    if slug == "inbound":
        try:
            storage_activo_mb = float(storage_activo_mb_inbound(db, negocio_id=int(negocio_id)))
            if storage_activo_mb < 0:
                storage_activo_mb = 0.0
        except Exception:
            storage_activo_mb = None  # resiliente

    # Convertimos dataclass -> dict serializable para template
    out: list[dict] = []
    for c in counters:
        row = {
            "key": c.key,
            "label": c.label,
            "unit": c.unit,
            "used": c.used,
            "limit": c.limit,
            "pct": c.pct,
            "is_limited": c.is_limited,
        }

        # ✅ Adjuntar storage activo SOLO al counter evidencias_mb
        if slug == "inbound" and c.key == "evidencias_mb":
            row["storage_active"] = storage_activo_mb  # float | None
            row["storage_active_unit"] = "mb"

        out.append(row)

    return out


@router.get("/planes", response_class=HTMLResponse)
async def app_planes_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    rol_real = (user.get("rol_real") or user.get("rol") or "").strip().lower()
    rol_efectivo = (user.get("rol") or "").strip().lower()
    acting_id = user.get("acting_negocio_id")

    negocio_id = _effective_negocio_id(user)
    if not negocio_id:
        logger.warning("[PLANES] usuario sin negocio_id efectivo. email=%s rol=%s", user.get("email"), rol_efectivo)
        return templates.TemplateResponse(
            "app/app_planes.html",
            {
                "request": request,
                "user": user,
                "snapshot": None,
                "negocio_ctx": None,
                "dashboard_modules": [],
                "is_admin": False,
                "is_superadmin": (rol_real == "superadmin"),
                "acting": bool(acting_id),
                "error": "No se encontró contexto de negocio en la sesión.",
            },
            status_code=400,
        )

    snapshot = get_entitlements_snapshot(db, negocio_id)
    snapshot_modules = (snapshot.get("modules") or {}) if isinstance(snapshot, dict) else {}
    negocio_ctx = (snapshot.get("negocio") or {}) if isinstance(snapshot, dict) else {}

    dashboard_modules: list[dict] = []
    for slug, mod in snapshot_modules.items():
        if not isinstance(mod, dict):
            continue

        # ✅ core no se muestra (es del sistema)
        if slug == "core":
            continue

        coming_soon = bool(mod.get("coming_soon", False))

        limits = mod.get("limits") if isinstance(mod.get("limits"), dict) else {}
        usage = mod.get("usage") if isinstance(mod.get("usage"), dict) else {}
        remaining = mod.get("remaining") if isinstance(mod.get("remaining"), dict) else {}

        mod_status = _norm_status(mod.get("status") or "inactive")
        sub = mod.get("subscription") if isinstance(mod.get("subscription"), dict) else {}
        ent_period = mod.get("period") if isinstance(mod.get("period"), dict) else {}

        status_final, sub_final, period_final = _normalize_subscription_for_ui(mod_status, sub, ent_period)

        if coming_soon:
            status_final = "coming_soon"
            sub_final = {}
            period_final = {}

        # ✅ Counters (ya calculados) + storage activo para inbound/evidencias_mb
        counters = [] if coming_soon else _counters_for_module(db=db, negocio_id=int(negocio_id), slug=slug, mod=mod)

        dashboard_modules.append(
            {
                "slug": slug,
                "enabled": (False if coming_soon else bool(mod.get("enabled"))),
                "status": status_final,
                "coming_soon": coming_soon,
                "period": period_final,
                "subscription": sub_final,
                # compat (por si tienes otras pantallas leyendo esto)
                "limits": _normalize_numeric_dict(limits),
                "usage": _normalize_numeric_dict(usage),
                "remaining": _normalize_numeric_dict(remaining),
                # nuevo (fuente de verdad UI)
                "counters": counters,
            }
        )

    order = {"inbound": 0, "wms": 1, "analytics_plus": 2, "ml_ia": 3}
    dashboard_modules.sort(key=lambda x: order.get(x["slug"], 50))

    is_admin = (rol_efectivo == "admin")

    logger.info(
        "[PLANES] view email=%s negocio_id=%s rol=%s superadmin=%s acting=%s",
        user.get("email"),
        negocio_id,
        rol_efectivo,
        (rol_real == "superadmin"),
        bool(acting_id),
    )

    return templates.TemplateResponse(
        "app/app_planes.html",
        {
            "request": request,
            "user": user,
            "snapshot": snapshot,
            "negocio_ctx": negocio_ctx,
            "dashboard_modules": dashboard_modules,
            "is_admin": is_admin,
            "is_superadmin": (rol_real == "superadmin"),
            "acting": bool(acting_id),
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/planes/volver")
async def app_planes_volver():
    return RedirectResponse(url="/app", status_code=303)
