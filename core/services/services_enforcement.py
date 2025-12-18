"""
services_enforcement.py – ORBION SaaS (enterprise)

✔ Decide ALLOW / WARN / BLOCK
✔ Centraliza reglas soft/hard
✔ Usa snapshot de entitlements (read-only)
✔ No incrementa usage
✔ No depende de Inbound / WMS
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from core.models.enums import ModuleKey, SubscriptionStatus
from core.services.services_entitlements import get_entitlements_snapshot


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
    period_end: Optional[object] = None  # datetime


# =========================================================
# POLITICA SOFT / HARD (V1)
# =========================================================
# Regla base:
# - TRIAL: siempre soft
# - ACTIVE:
#   - emprendedor: soft
#   - pyme / enterprise: hard
#
# Se puede refinar por métrica en el futuro.

HARD_SEGMENTS = {"pyme", "enterprise"}


# =========================================================
# API PUBLICA
# =========================================================

def check_limit(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
    delta: float = 1.0,
) -> EnforcementResult:
    """
    Decide si una acción que consume `metric_key` puede ejecutarse.

    Retorna EnforcementResult:
      - ALLOW: puede continuar
      - WARN: puede continuar, mostrar aviso
      - BLOCK: no ejecutar acción
    """

    snapshot = get_entitlements_snapshot(db, negocio_id)
    modules = snapshot.get("modules", {})
    mod = modules.get(module_key.value)

    # Módulo no contratado o no habilitado
    if not mod or not mod.get("enabled"):
        return EnforcementResult(
            decision=EnforcementDecision.BLOCK,
            reason=f"Módulo '{module_key.value}' no habilitado para el negocio.",
            metric_key=metric_key,
        )

    status = mod.get("status")
    segmento = snapshot.get("negocio", {}).get("segmento", "emprendedor")

    limits = mod.get("limits", {}) or {}
    usage = mod.get("usage", {}) or {}
    remaining = mod.get("remaining", {}) or {}
    period_end = mod.get("period", {}).get("end")

    # Métrica sin límite definido → ALLOW (feature sin cap)
    if metric_key not in limits:
        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Métrica sin límite definido.",
            metric_key=metric_key,
        )

    try:
        d = float(delta)
    except Exception:
        d = 0.0

    used = float(usage.get(metric_key, 0.0))
    limit = float(limits.get(metric_key, 0.0))
    rem = float(remaining.get(metric_key, max(0.0, limit - used)))

    # Si no hay consumo real, permitir
    if d <= 0:
        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Delta no positivo.",
            metric_key=metric_key,
            limit=limit,
            used=used,
            remaining=rem,
            period_end=period_end,
        )

    # Supera el límite
    exceeds = (used + d) > limit

    # =========================
    # REGLAS DE DECISION
    # =========================

    # TRIAL → siempre soft
    if status == SubscriptionStatus.TRIAL.value:
        if exceeds:
            return EnforcementResult(
                decision=EnforcementDecision.WARN,
                reason="Límite alcanzado durante trial.",
                metric_key=metric_key,
                limit=limit,
                used=used,
                remaining=rem,
                period_end=period_end,
            )
        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Dentro del límite (trial).",
            metric_key=metric_key,
            limit=limit,
            used=used,
            remaining=rem,
            period_end=period_end,
        )

    # ACTIVE
    if status == SubscriptionStatus.ACTIVE.value:
        # Emprendedor → soft
        if segmento not in HARD_SEGMENTS:
            if exceeds:
                return EnforcementResult(
                    decision=EnforcementDecision.WARN,
                    reason="Límite alcanzado.",
                    metric_key=metric_key,
                    limit=limit,
                    used=used,
                    remaining=rem,
                    period_end=period_end,
                )
            return EnforcementResult(
                decision=EnforcementDecision.ALLOW,
                reason="Dentro del límite.",
                metric_key=metric_key,
                limit=limit,
                used=used,
                remaining=rem,
                period_end=period_end,
            )

        # Pyme / Enterprise → hard
        if exceeds:
            return EnforcementResult(
                decision=EnforcementDecision.BLOCK,
                reason="Límite alcanzado para el período.",
                metric_key=metric_key,
                limit=limit,
                used=used,
                remaining=rem,
                period_end=period_end,
            )

        return EnforcementResult(
            decision=EnforcementDecision.ALLOW,
            reason="Dentro del límite.",
            metric_key=metric_key,
            limit=limit,
            used=used,
            remaining=rem,
            period_end=period_end,
        )

    # Estados no operativos
    return EnforcementResult(
        decision=EnforcementDecision.BLOCK,
        reason=f"Suscripción en estado '{status}'.",
        metric_key=metric_key,
        limit=limit,
        used=used,
        remaining=rem,
        period_end=period_end,
    )
