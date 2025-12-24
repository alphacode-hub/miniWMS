"""
services_entitlements.py – ORBION SaaS (enterprise, baseline aligned)

✅ Fuente única: Negocio.entitlements (verdad funcional)
✅ Segmentos oficiales: emprendedor / pyme / enterprise
✅ Límites por módulo (defaults por segmento + overrides por negocio)
✅ Legacy: plan_tipo solo como fallback -> se mapea al contrato nuevo
✅ Snapshot para Hub/Superadmin/Enforcement:
   - modules: enabled/status/period (desde entitlements)
   - limits por módulo (resueltos)
   - usage real por período (UsageCounter; solo módulos conocidos)
   - overlay SuscripcionModulo si existe (trial_ends, cancel_at_period_end, period*)
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Negocio
from core.models.enums import ModuleKey
from core.models.saas import SuscripcionModulo
from core.services.services_usage import list_usage_for_module_current_period


# =========================================================
# SEGMENTOS (canon)
# =========================================================
_ALLOWED_SEGMENTS = {"emprendedor", "pyme", "enterprise"}

# =========================================================
# STATUSES (canon)
# Nota: incluye "inactive" para módulos apagados funcionalmente.
# =========================================================
_ALLOWED_STATUSES = {"trial", "active", "past_due", "suspended", "cancelled", "inactive"}


# =========================================================
# DEFAULT LIMITS POR SEGMENTO (BASELINE)
# =========================================================
DEFAULT_LIMITS_BY_SEGMENT: dict[str, dict[str, dict[str, Any]]] = {
    "emprendedor": {
        "wms": {
            "usuarios_totales": 3,
            "productos": 300,
            "zonas": 10,
            "ubicaciones": 80,
            "slots": 800,
            "movimientos_mes": 10_000,
            "exportaciones_mes": 30,
        },
        "inbound": {
            "recepciones_mes": 30,
            "incidencias_mes": 1_000,
            "citas_mes": 300,
            "proveedores": 10,
            "evidencias_mb": 1_024,
        },
        "analytics_plus": {},
        "ml_ia": {},
    },
    "pyme": {
        "wms": {
            "usuarios_totales": 15,
            "productos": 3_000,
            "zonas": 80,
            "ubicaciones": 800,
            "slots": 8_000,
            "movimientos_mes": 200_000,
            "exportaciones_mes": 300,
        },
        "inbound": {
            "recepciones_mes": 100,
            "incidencias_mes": 10_000,
            "citas_mes": 3_000,
            "proveedores": 200,
            "evidencias_mb": 10_240,
        },
        "analytics_plus": {},
        "ml_ia": {},
    },
    "enterprise": {
        "wms": {
            "usuarios_totales": 200,
            "productos": 200_000,
            "zonas": 500,
            "ubicaciones": 50_000,
            "slots": 500_000,
            "movimientos_mes": 10_000_000,
            "exportaciones_mes": 10_000,
        },
        "inbound": {
            "recepciones_mes": 100_000,
            "incidencias_mes": 500_000,
            "citas_mes": 200_000,
            "proveedores": 50_000,
            "evidencias_mb": 200_000,
        },
        "analytics_plus": {},
        "ml_ia": {},
    },
}


# =========================================================
# DEFAULT ENTITLEMENTS (baseline)
# =========================================================
def default_entitlements() -> dict:
    segment = "emprendedor"
    base_limits = deepcopy(DEFAULT_LIMITS_BY_SEGMENT.get(segment, {}))

    return {
        "segment": segment,
        "modules": {
            "core": {"enabled": True, "status": "active"},
            "wms": {"enabled": True, "status": "active"},
            "inbound": {"enabled": False, "status": "inactive"},
            "analytics_plus": {"enabled": False, "status": "inactive"},
            "ml_ia": {"enabled": False, "status": "inactive"},
        },
        "limits": base_limits,
        "billing": {"source": "baseline"},
    }


# =========================================================
# HELPERS
# =========================================================
def _safe_str(x: Any, default: str = "") -> str:
    try:
        s = str(x).strip()
        return s if s else default
    except Exception:
        return default


def _norm_status(x: Any, default: str = "inactive") -> str:
    st = _safe_str(x, default).lower()
    return st if st in _ALLOWED_STATUSES else default


def _norm_segment(x: Any, default: str = "emprendedor") -> str:
    seg = _safe_str(x, default).lower()
    return seg if seg in _ALLOWED_SEGMENTS else default


def _module_alias(mk: str) -> str:
    mk = _safe_str(mk).lower()
    aliases = {
        "wms_core": "wms",
        "core_wms": "wms",
        "basic_wms": "wms",
        "inbound_orbion": "inbound",
    }
    return aliases.get(mk, mk)


def _looks_like_full_segment_limits(limits_in: dict) -> str | None:
    if not isinstance(limits_in, dict) or not limits_in:
        return None
    if not any(isinstance(v, dict) for v in limits_in.values()):
        return None

    norm_in: dict[str, dict[str, Any]] = {}
    for mk, lim in limits_in.items():
        mk_norm = _module_alias(mk)
        if isinstance(lim, dict):
            norm_in[mk_norm] = dict(lim)

    for seg, defaults in DEFAULT_LIMITS_BY_SEGMENT.items():
        if not isinstance(defaults, dict):
            continue

        ok = True
        for mk, dlim in defaults.items():
            if not isinstance(dlim, dict):
                continue
            in_lim = norm_in.get(mk)
            if in_lim is None:
                ok = False
                break

            for k, v in dlim.items():
                if k not in in_lim or in_lim.get(k) != v:
                    ok = False
                    break
            if not ok:
                break

        if ok:
            return seg

    return None


def _merge_limits_for_segment(segment: str, limits_in: Any, *, limits_overrides: Any = None) -> dict[str, dict[str, Any]]:
    base = deepcopy(DEFAULT_LIMITS_BY_SEGMENT.get(segment, {}))

    effective_overrides = limits_overrides if isinstance(limits_overrides, dict) else None
    if effective_overrides is None:
        effective_overrides = limits_in if isinstance(limits_in, dict) else None

    if not isinstance(effective_overrides, dict) or not effective_overrides:
        return base

    seg_match = _looks_like_full_segment_limits(effective_overrides)
    if seg_match is not None and seg_match != segment:
        logger.info(
            "[ENTITLEMENTS] limits snapshot detectado (seg=%s) pero segmento actual=%s -> ignorando snapshot para evitar límites congelados",
            seg_match,
            segment,
        )
        return base

    any_module_dict = any(isinstance(v, dict) for v in effective_overrides.values())
    if any_module_dict:
        for mk, lim in effective_overrides.items():
            mk_norm = _module_alias(mk)
            if not isinstance(lim, dict):
                continue
            base.setdefault(mk_norm, {})
            base[mk_norm].update(lim)
        return base

    base.setdefault("wms", {})
    base["wms"].update(effective_overrides)
    return base


# =========================================================
# LEGACY PLAN -> CONTRATO NUEVO (fallback)
# =========================================================
def _map_legacy_plan_tipo(plan_tipo: str) -> dict:
    p = _safe_str(plan_tipo, "demo").lower()
    if p in {"enterprise", "ent"}:
        seg = "enterprise"
    elif p in {"pyme", "pro"}:
        seg = "pyme"
    else:
        seg = "emprendedor"

    ent = default_entitlements()
    ent["segment"] = seg
    ent["limits"] = deepcopy(DEFAULT_LIMITS_BY_SEGMENT.get(seg, {}))

    ent["modules"]["wms"] = {"enabled": True, "status": "active"}
    ent["modules"]["inbound"] = {"enabled": False, "status": "inactive"}

    ent["billing"] = {"source": "legacy", "plan_tipo": p}
    return ent


# =========================================================
# NORMALIZACIÓN
# =========================================================
def normalize_entitlements(ent: Optional[dict]) -> dict:
    base = default_entitlements()

    if not isinstance(ent, dict) or not ent:
        return deepcopy(base)

    out = deepcopy(base)

    out["segment"] = _norm_segment(ent.get("segment"), out["segment"])

    billing = ent.get("billing")
    if isinstance(billing, dict):
        out["billing"].update(billing)

    modules_in = ent.get("modules")
    if isinstance(modules_in, dict):
        out_modules: dict[str, dict[str, Any]] = {}
        for k, v in modules_in.items():
            mk = _module_alias(k)
            mv = v if isinstance(v, dict) else {}
            enabled = bool(mv.get("enabled", False))
            status = _norm_status(mv.get("status", "inactive"))

            mod_out: dict[str, Any] = {"enabled": enabled, "status": status}

            period = mv.get("period")
            if isinstance(period, dict):
                mod_out["period"] = {"from": period.get("from"), "to": period.get("to")}

            out_modules[mk] = mod_out

        out["modules"].update(out_modules)

    out["modules"].setdefault("core", {"enabled": True, "status": "active"})
    out["modules"]["core"]["enabled"] = True
    out["modules"]["core"]["status"] = "active"

    limits_overrides = ent.get("limits_overrides")
    out["limits"] = _merge_limits_for_segment(out["segment"], ent.get("limits"), limits_overrides=limits_overrides)

    return out


# =========================================================
# RESOLVE (fuente única)
# =========================================================
def resolve_entitlements(negocio: Negocio) -> dict:
    ent = getattr(negocio, "entitlements", None)
    if isinstance(ent, dict) and ent:
        return normalize_entitlements(ent)

    plan = _safe_str(getattr(negocio, "plan_tipo", "demo"), "demo").lower()
    logger.warning("[ENTITLEMENTS] negocio %s sin entitlements persistidos (fallback plan_tipo=%s)", negocio.id, plan)
    return normalize_entitlements(_map_legacy_plan_tipo(plan))


# =========================================================
# HELPERS PUBLICOS
# =========================================================
def has_module(negocio: Negocio, module_key: str, *, require_active: bool = True) -> bool:
    ent = resolve_entitlements(negocio)
    mk = _module_alias(module_key)
    mod = (ent.get("modules") or {}).get(mk)

    if not mod or not bool(mod.get("enabled")):
        return False

    if not require_active:
        return True

    st = _safe_str(mod.get("status"), "").lower()
    return st in {"active", "trial"}


def get_module(ent: dict, module_key: str) -> dict:
    mk = _module_alias(module_key)
    mod = (ent.get("modules") or {}).get(mk) or {}
    return mod if isinstance(mod, dict) else {}


# =========================================================
# SNAPSHOT PARA HUB / SUPERADMIN / ENFORCEMENT
# =========================================================

def _sub_to_overlay(sub: SuscripcionModulo | None) -> dict[str, Any]:
    if not sub:
        return {}

    def _v(x: Any) -> Any:
        try:
            return x.isoformat() if x else None
        except Exception:
            return None

    mk = getattr(sub.module_key, "value", str(sub.module_key))
    st = getattr(sub.status, "value", str(sub.status))

    return {
        "subscription": {
            "module_key": str(mk).lower(),
            "status": str(st).lower(),
            "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", 0)),
            "trial_ends_at": _v(getattr(sub, "trial_ends_at", None)),
            "period": {
                "start": _v(getattr(sub, "current_period_start", None)),
                "end": _v(getattr(sub, "current_period_end", None)),
            },
        }
    }


def _effective_enabled_status(
    *,
    ent_enabled: bool,
    ent_status: str,
    sub: SuscripcionModulo | None,
) -> tuple[bool, str]:
    """
    Contrato enterprise:
    - entitlements define provisioning (enabled/disabled)
    - subscription define acceso comercial real (trial/active/past_due/suspended/cancelled)
    - estado efectivo y acceso = overlay de subscription cuando existe
    """
    ent_status_norm = _norm_status(ent_status, "inactive")

    if not sub:
        # No hay suscripción -> usar entitlements tal cual
        return bool(ent_enabled), ent_status_norm

    sub_status = getattr(sub.status, "value", str(sub.status)).lower().strip()
    sub_status = _norm_status(sub_status, "inactive")

    # Solo TRIAL/ACTIVE cuentan como acceso. El resto bloquea.
    sub_allows = sub_status in {"trial", "active"}

    effective_enabled = bool(ent_enabled) and bool(sub_allows)
    effective_status = sub_status  # lo que “manda” para UI/decisiones

    return effective_enabled, effective_status


def get_entitlements_snapshot(db: Session, negocio_id: int) -> Dict[str, Any]:
    n = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not n:
        ent0 = normalize_entitlements(None)
        return {"negocio": {"id": negocio_id}, "entitlements": ent0, "modules": {}}

    ent = resolve_entitlements(n)
    limits_all = ent.get("limits") if isinstance(ent.get("limits"), dict) else {}

    subs = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .all()
    )
    subs_by_key: dict[str, SuscripcionModulo] = {}
    for s in subs:
        k = getattr(s.module_key, "value", str(s.module_key))
        subs_by_key[str(k).lower()] = s

    modules_out: dict[str, Any] = {}

    for mk, mod in (ent.get("modules") or {}).items():
        mk_norm = _module_alias(mk)

        ent_enabled = bool(mod.get("enabled"))
        ent_status = _norm_status(mod.get("status", "inactive"))

        sub = subs_by_key.get(mk_norm)
        overlay = _sub_to_overlay(sub)

        # ✅ estado/enable EFECTIVO (sin ambigüedad)
        effective_enabled, effective_status = _effective_enabled_status(
            ent_enabled=ent_enabled,
            ent_status=ent_status,
            sub=sub,
        )

        # usage real: solo si hay sub + module_key conocido (wms/inbound)
        usage_raw: dict[str, Any] = {}
        if sub:
            mk_enum = _as_modulekey_or_none(mk_norm)
            if mk_enum:
                try:
                    usage_raw = list_usage_for_module_current_period(db, negocio_id, mk_enum) or {}
                except Exception:
                    usage_raw = {}

        limits_raw: dict[str, Any] = {}
        if isinstance(limits_all, dict) and isinstance(limits_all.get(mk_norm), dict):
            limits_raw = limits_all.get(mk_norm) or {}

        limits = _cast_numeric_dict(limits_raw)
        usage = _cast_numeric_dict(usage_raw)

        remaining: dict[str, Any] = {}
        if limits:
            for k, lim in limits.items():
                try:
                    limit_v = float(lim)
                except Exception:
                    continue
                try:
                    used_v = float(usage.get(k, 0) or 0)
                except Exception:
                    used_v = 0.0
                rem_v = max(0.0, limit_v - used_v)
                remaining[str(k)] = _num_cast(rem_v)

        payload: dict[str, Any] = {
            # 👇 lo que consume UI/Hub/Plan Center (coherente)
            "enabled": effective_enabled,
            "status": effective_status,

            # 👇 opcional: mostrar/debug (si te sirve)
            "ent_enabled": ent_enabled,
            "ent_status": ent_status,
        }

        # period funcional desde entitlements (si lo usas)
        if "period" in mod and isinstance(mod.get("period"), dict):
            payload["period"] = mod.get("period")

        payload["limits"] = limits
        payload["usage"] = usage
        payload["remaining"] = remaining

        # overlay comercial (trial/period/cancel flag)
        payload.update(overlay)

        modules_out[mk_norm] = payload

    return {
        "negocio": {
            "id": n.id,
            "nombre": getattr(n, "nombre_fantasia", None) or getattr(n, "nombre", None) or f"Negocio #{n.id}",
            "segment": ent.get("segment", "emprendedor"),
        },
        "entitlements": ent,
        "modules": modules_out,
    }


def _as_modulekey_or_none(mk_norm: str) -> ModuleKey | None:
    mk_norm = _module_alias(mk_norm)
    if mk_norm == ModuleKey.INBOUND.value:
        return ModuleKey.INBOUND
    if mk_norm == ModuleKey.WMS.value:
        return ModuleKey.WMS
    return None


def _num_cast(v: Any) -> Any:
    try:
        if v is None:
            return None
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return int(round(f))
        return f
    except Exception:
        return v


def _cast_numeric_dict(d: Any) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[str(k)] = _num_cast(v)
    return out


def has_module_db(
    db: Session,
    negocio_id: int,
    module_key: str,
    *,
    require_active: bool = True,
) -> bool:
    snap = get_entitlements_snapshot(db, negocio_id)
    mk = _module_alias(module_key)
    mod = (snap.get("modules") or {}).get(mk) or {}
    if not isinstance(mod, dict):
        return False

    enabled = bool(mod.get("enabled"))
    if not enabled:
        return False

    if not require_active:
        return True

    st = _safe_str(mod.get("status"), "inactive").lower()
    return st in {"trial", "active"}
