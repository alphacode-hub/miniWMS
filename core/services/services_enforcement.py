# core/services/services_enforcement.py
"""
services_enforcement.py – ORBION SaaS (enterprise, baseline aligned)

✅ Decide ALLOW / WARN / BLOCK
✅ Centraliza reglas soft/hard
✅ Usa snapshot de entitlements (read-only)
✅ No incrementa usage
✅ No depende de Inbound / WMS
✅ Auditoría enterprise v2.1 en WARN/BLOCK (si actor disponible)

Notas:
- No depende de SubscriptionStatus Enum para comparar (snapshot status viene como string)
- Soporta module_key como ModuleKey o str ("inbound"/"wms")
- Límite <= 0: se interpreta como "sin cap" (ALLOW) para evitar blocks accidentales
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any, Union

from sqlalchemy.orm import Session

from core.models.enums import ModuleKey
from core.services.services_entitlements import get_entitlements_snapshot
from core.services.services_audit import audit, AuditAction


# =========================================================
# RESULTADO DE ENFORCEMENT
# =========================================================

class EnforcementDecision(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class EnforcementResult:
    decision: EnforcementDecision
    reason: str
    metric_key: Optional[str] = None
    limit: Optional[float] = None
    used: Optional[float] = None
    remaining: Optional[float] = None
    period_end: Optional[Any] = None  # datetime | str | None


# =========================================================
# POLITICA SOFT / HARD (V1)
# =========================================================
HARD_SEGMENTS = {"pyme", "enterprise"}

_ALLOWED_ACTIVE = "active"
_ALLOWED_TRIAL = "trial"


# =========================================================
# HELPERS
# =========================================================

def _safe_str(x: object, default: str = "") -> str:
    try:
        s = str(x).strip()
        return s if s else default
    except Exception:
        return default


def _module_key_str(mk: Union[ModuleKey, str]) -> str:
    if isinstance(mk, ModuleKey):
        return mk.value
    return _safe_str(mk, "").lower()


def _norm_metric(metric_key: str) -> str:
    return _safe_str(metric_key, "").lower()


def _get_period_end(mod: dict) -> Optional[Any]:
    """
    period_end para UX:
    - Preferimos overlay de suscripción si existe (subscription.period.end)
    - Si no existe, usamos entitlements.period.to / end
    """
    sub = mod.get("subscription") or {}
    if isinstance(sub, dict):
        per = sub.get("period") or {}
        if isinstance(per, dict) and per.get("end"):
            return per.get("end")

    per2 = mod.get("period") or {}
    if isinstance(per2, dict):
        if per2.get("to"):
            return per2.get("to")
        if per2.get("end"):
            return per2.get("end")

    return None


def _audit_enforcement(
    db: Session,
    *,
    actor: Optional[dict],
    decision: EnforcementDecision,
    negocio_id: int,
    module_key: str,
    metric_key: str,
    payload: dict,
) -> None:
    """
    Auditoría v2.1 solo si actor viene disponible.
    Nunca rompe flujo.
    """
    if not actor:
        return
    try:
        action = None
        if decision == EnforcementDecision.BLOCK:
            action = getattr(AuditAction, "ENFORCEMENT_BLOCK", None)
        elif decision == EnforcementDecision.WARN:
            action = getattr(AuditAction, "ENFORCEMENT_WARN", None)

        if not action:
            return

        audit(
            db=db,
            actor=actor,
            action=action,
            payload={
                "negocio_id": negocio_id,
                "module": module_key,
                "metric": metric_key,
                **payload,
            },
            commit=False,
        )
    except Exception:
        return


# =========================================================
# API PUBLICA
# =========================================================

def check_limit(
    db: Session,
    negocio_id: int,
    module_key: Union[ModuleKey, str],
    metric_key: str,
    delta: float = 1.0,
    *,
    actor: Optional[dict] = None,
) -> EnforcementResult:
    """
    Decide si una acción que consume `metric_key` puede ejecutarse.

    Retorna:
      - ALLOW: puede continuar
      - WARN: puede continuar, mostrar aviso
      - BLOCK: no ejecutar acción
    """
    mk = _module_key_str(module_key)
    metric = _norm_metric(metric_key)

    if not mk or not metric:
        res = EnforcementResult(
            decision=EnforcementDecision.BLOCK,
            reason="Parámetros inválidos (module_key/metric_key).",
            metric_key=metric_key,
        )
        _audit_enforcement(db, actor=actor, decision=res.decision, negocio_id=negocio_id, module_key=mk, metric_key=metric, payload={"reason": res.reason})
        return res

    snapshot = get_entitlements_snapshot(db, negocio_id)

    modules = snapshot.get("modules", {}) or {}
    mod = modules.get(mk)

    # Módulo no contratado / no habilitado
    if not mod or not mod.get("enabled"):
        res = EnforcementResult(
            decision=EnforcementDecision.BLOCK,
            reason=f"Módulo '{mk}' no habilitado para el negocio.",
            metric_key=metric,
            period_end=_get_period_end(mod or {}),
        )
        _audit_enforcement(db, actor=actor, decision=res.decision, negocio_id=negocio_id, module_key=mk, metric_key=metric, payload={"reason": res.reason})
        return res

    status = _safe_str(mod.get("status"), "inactive").lower()

    # Segmento: fuente única es entitlements.segment
    segmento = _safe_str(
        (snapshot.get("entitlements") or {}).get("segment")
        or (snapshot.get("negocio") or {}).get("segment")
        or "emprendedor",
        "emprendedor",
    ).lower()

    limits = mod.get("limits", {}) or {}
    usage = mod.get("usage", {}) or {}
    remaining_map = mod.get("remaining", {}) or {}
    period_end = _get_period_end(mod)

    # Métrica sin límite → ALLOW
    if metric not in limits:
        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Métrica sin límite definido.",
            metric_key=metric,
            period_end=period_end,
        )

    # delta defensivo
    try:
        d = float(delta)
    except Exception:
        d = 0.0

    used = float(usage.get(metric, 0.0) or 0.0)

    try:
        limit = float(limits.get(metric, 0.0) or 0.0)
    except Exception:
        limit = 0.0

    # limit <= 0 => sin cap
    if limit <= 0:
        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Límite no aplicable (<= 0).",
            metric_key=metric,
            limit=limit,
            used=used,
            remaining=None,
            period_end=period_end,
        )

    # remaining defensivo (pre-delta)
    try:
        rem0 = float(remaining_map.get(metric, max(0.0, limit - used)) or 0.0)
    except Exception:
        rem0 = max(0.0, limit - used)

    if d <= 0:
        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Delta no positivo.",
            metric_key=metric,
            limit=limit,
            used=used,
            remaining=rem0,
            period_end=period_end,
        )

    post_used = used + d
    exceeds = post_used > limit
    # remaining post-delta (UX)
    rem = max(0.0, limit - post_used)

    # =========================
    # REGLAS DE DECISION
    # =========================

    # trial -> siempre soft
    if status == _ALLOWED_TRIAL:
        if exceeds:
            res = EnforcementResult(
                decision=EnforcementDecision.WARN,
                reason="Límite alcanzado durante trial.",
                metric_key=metric,
                limit=limit,
                used=used,
                remaining=rem,
                period_end=period_end,
            )
            _audit_enforcement(db, actor=actor, decision=res.decision, negocio_id=negocio_id, module_key=mk, metric_key=metric, payload={"status": status, "segment": segmento, "limit": limit, "used": used, "delta": d})
            return res

        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Dentro del límite (trial).",
            metric_key=metric,
            limit=limit,
            used=used,
            remaining=rem,
            period_end=period_end,
        )

    # active
    if status == _ALLOWED_ACTIVE:
        # Soft segments
        if segmento not in HARD_SEGMENTS:
            if exceeds:
                res = EnforcementResult(
                    decision=EnforcementDecision.WARN,
                    reason="Límite alcanzado.",
                    metric_key=metric,
                    limit=limit,
                    used=used,
                    remaining=rem,
                    period_end=period_end,
                )
                _audit_enforcement(db, actor=actor, decision=res.decision, negocio_id=negocio_id, module_key=mk, metric_key=metric, payload={"status": status, "segment": segmento, "limit": limit, "used": used, "delta": d})
                return res

            return EnforcementResult(
                decision=EnforcementDecision.ALLOW,
                reason="Dentro del límite.",
                metric_key=metric,
                limit=limit,
                used=used,
                remaining=rem,
                period_end=period_end,
            )

        # Hard segments (pyme/enterprise)
        if exceeds:
            res = EnforcementResult(
                decision=EnforcementDecision.BLOCK,
                reason="Límite alcanzado para el período.",
                metric_key=metric,
                limit=limit,
                used=used,
                remaining=rem,
                period_end=period_end,
            )
            _audit_enforcement(db, actor=actor, decision=res.decision, negocio_id=negocio_id, module_key=mk, metric_key=metric, payload={"status": status, "segment": segmento, "limit": limit, "used": used, "delta": d})
            return res

        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Dentro del límite.",
            metric_key=metric,
            limit=limit,
            used=used,
            remaining=rem,
            period_end=period_end,
        )

    # Estados no operativos: past_due/suspended/cancelled/inactive/...
    res = EnforcementResult(
        decision=EnforcementDecision.BLOCK,
        reason=f"Suscripción en estado '{status}'.",
        metric_key=metric,
        limit=limit,
        used=used,
        remaining=rem0,  # aquí es más honesto reportar remaining pre-delta
        period_end=period_end,
    )
    _audit_enforcement(db, actor=actor, decision=res.decision, negocio_id=negocio_id, module_key=mk, metric_key=metric, payload={"status": status, "segment": segmento, "limit": limit, "used": used, "delta": d, "reason": res.reason})
    return res
