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

    subs: List[SuscripcionModulo] = (
        db.query(SuscripcionModulo)
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

            renew_subscription(db, sub)

            # renew_subscription puede setear CANCELLED si cancel_at_period_end=1
            if sub.status == SubscriptionStatus.CANCELLED and before_status != SubscriptionStatus.CANCELLED:
                cancelled += 1
                logger.info(
                    "[JOB] subscription cancelled id=%s negocio_id=%s module=%s",
                    sub.id,
                    sub.negocio_id,
                    getattr(sub.module_key, "value", str(sub.module_key)),
                )
            else:
                renewed += 1
                logger.info(
                    "[JOB] subscription renewed id=%s negocio_id=%s module=%s status=%s",
                    sub.id,
                    sub.negocio_id,
                    getattr(sub.module_key, "value", str(sub.module_key)),
                    getattr(sub.status, "value", str(sub.status)),
                )

            db.commit()

        except Exception as e:
            db.rollback()
            errors += 1

            # ✅ Observability real: dejamos trazabilidad por suscripción
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
