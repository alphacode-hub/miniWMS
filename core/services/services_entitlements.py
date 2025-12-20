# core/services/services_entitlements.py
"""
services_entitlements.py – ORBION SaaS (enterprise)

✅ Fuente única: Negocio.entitlements (verdad funcional)
✅ Segmentos oficiales: emprendedor / pyme / enterprise
✅ Límites por módulo, basados en defaults por segmento (con overrides por negocio)
✅ Legacy: plan_tipo solo como fallback interno -> se mapea al contrato nuevo
✅ Snapshot para Hub/Superadmin/Enforcement:
   - modules: enabled/status/period
   - limits por módulo (ya resueltos por segmento + overrides)
   - usage real por período (desde UsageCounter, solo módulos conocidos)
   - overlay de SuscripcionModulo si existe (trial_ends, cancel_at_period_end, current_period_*)
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

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
# =========================================================
_ALLOWED_STATUSES = {"trial", "active", "past_due", "suspended", "cancelled", "inactive"}


# =========================================================
# DEFAULT LIMITS POR SEGMENTO (BASELINE)
# Puedes ajustar números cuando quieras, pero el CONTRATO queda estable.
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
            "recepciones_mes": 200,
            "incidencias_mes": 1_000,
            "citas_mes": 300,
            "proveedores": 50,
            "evidencias_mb": 1_024,
        },
        # futuros módulos (si los activas)
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
            "recepciones_mes": 2_000,
            "incidencias_mes": 10_000,
            "citas_mes": 3_000,
            "proveedores": 500,
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
# - core: SIEMPRE enabled/active (plataforma)
# - wms: base product (miniWMS) -> enabled/active por defecto
# - inbound: depende del negocio (por defecto disabled)
# - limits: se resuelven por segmento + overrides
# =========================================================
def default_entitlements() -> dict:
    segment = "emprendedor"
    base_limits = deepcopy(DEFAULT_LIMITS_BY_SEGMENT.get(segment, {}))

    return {
        "segment": segment,
        "modules": {
            "core": {"enabled": True, "status": "active"},
            "wms": {"enabled": False, "status": "inactive"},
            "inbound": {"enabled": True, "status": "inactive"},
            "analytics_plus": {"enabled": False, "status": "inactive"},
            "ml_ia": {"enabled": False, "status": "inactive"},
        },
        # limits por módulo (formato recomendado)
        "limits": base_limits,
        "billing": {"source": "baseline"},
    }


# =========================================================
# LEGACY FALLBACK (opcional, interno)
# Mapea tu plan_tipo legacy -> contrato nuevo
# =========================================================
LEGACY_PLAN_MAP: dict[str, dict] = {
    "demo": {
        "segment": "emprendedor",
        "modules": {
            "core": {"enabled": True, "status": "active"},
            "wms": {"enabled": True, "status": "active"},
            "inbound": {"enabled": True, "status": "trial"},
        },
        "limits": {},  # si vacío => se rellena por defaults del segmento
        "billing": {"source": "legacy"},
    },
    "full": {
        "segment": "pyme",
        "modules": {
            "core": {"enabled": True, "status": "active"},
            "wms": {"enabled": True, "status": "active"},
            "inbound": {"enabled": True, "status": "active"},
        },
        "limits": {},
        "billing": {"source": "legacy"},
    },
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
    """
    Normaliza nombres históricos a claves canónicas.
    - wms_core -> wms
    - core_wms -> wms
    - inbound_orbion -> inbound
    """
    mk = _safe_str(mk).lower()
    aliases = {
        "wms_core": "wms",
        "core_wms": "wms",
        "basic_wms": "wms",
        "inbound_orbion": "inbound",
    }
    return aliases.get(mk, mk)


def _merge_limits_for_segment(segment: str, limits_in: Any) -> dict[str, dict[str, Any]]:
    """
    Resuelve limits por módulo:
    - Defaults por segmento
    - Overrides por negocio en entitlements["limits"] (si vienen)
      Soporta:
        A) limits por módulo: {"inbound": {...}, "wms": {...}}
        B) (legacy) limits planos: {"usuarios_totales": 3, ...} -> se asignan a "wms"
    """
    base = deepcopy(DEFAULT_LIMITS_BY_SEGMENT.get(segment, {}))

    if not isinstance(limits_in, dict) or not limits_in:
        return base

    # Caso A: por módulo
    any_module_dict = any(isinstance(v, dict) for v in limits_in.values())
    if any_module_dict:
        for mk, lim in limits_in.items():
            mk_norm = _module_alias(mk)
            if not isinstance(lim, dict):
                continue
            base.setdefault(mk_norm, {})
            base[mk_norm].update(lim)
        return base

    # Caso B: plano -> asumir WMS
    base.setdefault("wms", {})
    base["wms"].update(limits_in)
    return base


# =========================================================
# NORMALIZACIÓN (contrato estable)
# =========================================================
def normalize_entitlements(ent: Optional[dict]) -> dict:
    """
    Garantiza estructura estándar mínima:
      segment, modules, limits, billing
    Y fuerza core enabled/active.
    Además:
    - segment en {emprendedor, pyme, enterprise}
    - module keys normalizadas (aliases)
    - limits siempre quedan por módulo (dict de dict)
    """
    base = default_entitlements()

    if not isinstance(ent, dict) or not ent:
        return deepcopy(base)

    out = deepcopy(base)

    # segment
    out["segment"] = _norm_segment(ent.get("segment"), out["segment"])

    # billing
    billing = ent.get("billing")
    if isinstance(billing, dict):
        out["billing"].update(billing)

    # modules
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

        # overlay sobre defaults
        out["modules"].update(out_modules)

    # core obligatorio
    out["modules"].setdefault("core", {"enabled": True, "status": "active"})
    out["modules"]["core"]["enabled"] = True
    out["modules"]["core"]["status"] = "active"

    # limits: resolver defaults por segmento + overrides
    out["limits"] = _merge_limits_for_segment(out["segment"], ent.get("limits"))

    return out


# =========================================================
# RESOLVE (fuente única)
# =========================================================
def resolve_entitlements(negocio: Negocio) -> dict:
    ent = getattr(negocio, "entitlements", None)
    if isinstance(ent, dict) and ent:
        return normalize_entitlements(ent)

    # DEBUG opcional
    logger.warning("[ENTITLEMENTS] negocio %s sin entitlements persistidos", negocio.id)

    plan = _safe_str(getattr(negocio, "plan_tipo", "demo"), "demo").lower()
    legacy = LEGACY_PLAN_MAP.get(plan)
    if legacy:
        return normalize_entitlements(legacy)

    return normalize_entitlements(None)

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

    return _safe_str(mod.get("status"), "").lower() == "active"


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


def _as_modulekey_or_none(mk_norm: str) -> ModuleKey | None:
    """
    Convierte a ModuleKey SOLO si corresponde a enums actuales (usage).
    - "wms" -> ModuleKey.WMS
    - "inbound" -> ModuleKey.INBOUND
    """
    mk_norm = _module_alias(mk_norm)
    if mk_norm == ModuleKey.INBOUND.value:
        return ModuleKey.INBOUND
    if mk_norm == ModuleKey.WMS.value:
        return ModuleKey.WMS
    return None


def get_entitlements_snapshot(db: Session, negocio_id: int) -> Dict[str, Any]:
    """
    Snapshot canónico:
      negocio: {id, nombre, segment}
      entitlements: (normalizado)
      modules: dict por módulo con:
        - enabled/status/period (desde entitlements)
        - limits (desde entitlements.limits[módulo])
        - usage (desde UsageCounter si hay suscripción y module_key conocido)
        - overlay subscription (si existe)
        - remaining (si limits y usage están)
    """
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
        enabled = bool(mod.get("enabled"))
        status = _norm_status(mod.get("status", "inactive"))

        sub = subs_by_key.get(mk_norm)
        overlay = _sub_to_overlay(sub)

        # usage real: solo si hay suscripción + module_key conocido (wms/inbound)
        usage: dict[str, float] = {}
        if sub:
            mk_enum = _as_modulekey_or_none(mk_norm)
            if mk_enum:
                try:
                    usage = list_usage_for_module_current_period(db, negocio_id, mk_enum)
                except Exception:
                    usage = {}

        # limits por módulo
        limits: dict[str, Any] = {}
        if isinstance(limits_all, dict) and isinstance(limits_all.get(mk_norm), dict):
            limits = limits_all.get(mk_norm) or {}

        # remaining
        remaining: dict[str, float] = {}
        if limits and usage:
            for k, lim in limits.items():
                try:
                    limit_v = float(lim)
                except Exception:
                    continue
                used_v = float(usage.get(k, 0.0) or 0.0)
                remaining[k] = max(0.0, limit_v - used_v)

        payload: dict[str, Any] = {"enabled": enabled, "status": status}

        if "period" in mod and isinstance(mod.get("period"), dict):
            payload["period"] = mod.get("period")

        payload["limits"] = limits
        payload["usage"] = usage
        payload["remaining"] = remaining
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
