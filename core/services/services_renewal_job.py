# core/services/services_renewal_job.py
"""
services_renewal_job.py – ORBION SaaS (enterprise)

✔ Job de renovación de suscripciones por módulo
✔ Encuentra suscripciones vencidas (next_renewal_at <= now)
✔ Ejecuta renew_subscription (trial->active, cancel_at_period_end, etc.)
✔ Multi-DB friendly (SQLite/Postgres)
✔ Observability: log por resultado + log de errores por suscripción
✔ No toca Inbound/WMS
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models.saas import SuscripcionModulo
from core.models.enums import SubscriptionStatus
from core.services.services_subscriptions import renew_subscription
from core.logging_config import logger


@dataclass
class RenewalJobResult:
    checked: int
    renewed: int
    cancelled: int
    errors: int
    subscription_ids: list[int]


def run_subscription_renewal_job(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> RenewalJobResult:
    """
    Ejecuta renovación para suscripciones que requieren acción.

    - Renueva periodos cuando next_renewal_at <= now
    - Aplica cancel_at_period_end dentro de renew_subscription
    - commit por suscripción para que una falla no tumbe todo el job
    """
    ts = now or utcnow()

    # ✅ defensivo: next_renewal_at NULL no entra al job
    subs: List[SuscripcionModulo] = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.next_renewal_at.isnot(None))
        .filter(SuscripcionModulo.next_renewal_at <= ts)
        .filter(SuscripcionModulo.status != SubscriptionStatus.CANCELLED)
        .order_by(SuscripcionModulo.next_renewal_at.asc())
        .limit(limit)
        .all()
    )

    checked = len(subs)
    renewed = 0
    cancelled = 0
    errors = 0
    ids: list[int] = []

    logger.info(
        "[JOB] renew_subscriptions start now=%s limit=%s found=%s",
        ts.isoformat(),
        limit,
        checked,
    )

    for sub in subs:
        ids.append(int(sub.id))
        try:
            before_status = sub.status
            before_period_end = getattr(sub, "current_period_end", None)

            # renew_subscription debe:
            # - mover período si corresponde
            # - setear next_renewal_at
            # - aplicar cancel_at_period_end
            renew_subscription(db, sub)

            after_status = sub.status
            after_period_end = getattr(sub, "current_period_end", None)

            # commit por suscripción (aislamos fallos)
            db.commit()

            # renew_subscription puede setear CANCELLED si cancel_at_period_end=1
            if after_status == SubscriptionStatus.CANCELLED and before_status != SubscriptionStatus.CANCELLED:
                cancelled += 1
                logger.info(
                    "[JOB] subscription cancelled id=%s negocio_id=%s module=%s",
                    sub.id,
                    sub.negocio_id,
                    getattr(sub.module_key, "value", str(sub.module_key)),
                )
                continue

            # Si no fue cancelada, consideramos “renewed” cuando hubo avance real de período
            if after_period_end and before_period_end and after_period_end != before_period_end:
                renewed += 1
                logger.info(
                    "[JOB] subscription renewed id=%s negocio_id=%s module=%s status=%s",
                    sub.id,
                    sub.negocio_id,
                    getattr(sub.module_key, "value", str(sub.module_key)),
                    getattr(after_status, "value", str(after_status)),
                )
            else:
                # No necesariamente es error: podría ser que renew_subscription no movió período por política.
                logger.info(
                    "[JOB] subscription processed (no period change) id=%s negocio_id=%s module=%s status=%s",
                    sub.id,
                    sub.negocio_id,
                    getattr(sub.module_key, "value", str(sub.module_key)),
                    getattr(after_status, "value", str(after_status)),
                )

        except Exception as e:
            db.rollback()
            errors += 1

            # ✅ Observability real: trazabilidad por suscripción
            logger.exception(
                "[JOB] renew_subscriptions error id=%s negocio_id=%s module=%s err=%s",
                getattr(sub, "id", None),
                getattr(sub, "negocio_id", None),
                getattr(getattr(sub, "module_key", None), "value", str(getattr(sub, "module_key", None))),
                str(e),
            )

    logger.info(
        "[JOB] renew_subscriptions end checked=%s renewed=%s cancelled=%s errors=%s",
        checked,
        renewed,
        cancelled,
        errors,
    )

    return RenewalJobResult(
        checked=checked,
        renewed=renewed,
        cancelled=cancelled,
        errors=errors,
        subscription_ids=ids,
    )
