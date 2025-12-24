# core/services/services_renewal_job.py
"""
services_renewal_job.py – ORBION SaaS (compat wrapper)

⚠️ Deprecated:
Este archivo queda como wrapper para no romper imports legacy.
La lógica real vive en services_subscription_jobs.py (Contrato V1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from core.services.services_subscription_jobs import run_subscriptions_job


@dataclass
class RenewalJobResult:
    checked: int
    renewed: int
    cancelled: int
    errors: int
    subscription_ids: list[int]  # legacy, lo dejamos aunque el job nuevo no lo entregue


def run_subscription_renewal_job(
    db: Session,
    *,
    now: Optional[datetime] = None,
    limit: int = 500,
) -> RenewalJobResult:
    """
    Compat: llama al job nuevo y traduce resultado.
    """
    r = run_subscriptions_job(
        db,
        now=now,
        batch_size=limit,
        lookahead_minutes=5,
        commit=True,
    )

    c = (r.get("counters") or {})
    checked = int(c.get("scanned", 0))
    renewed = int(c.get("renewed", 0) + c.get("repaired", 0))
    cancelled = int(c.get("cancelled", 0))
    errors = int(c.get("errors", 0))

    return RenewalJobResult(
        checked=checked,
        renewed=renewed,
        cancelled=cancelled,
        errors=errors,
        subscription_ids=[],
    )
