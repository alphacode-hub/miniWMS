# core/services/services_renewal_job.py
"""
services_renewal_job.py – ORBION SaaS (enterprise, baseline aligned)

✔ Job de renovación de suscripciones por módulo
✔ Encuentra suscripciones vencidas (next_renewal_at <= now)
✔ Ejecuta renew_subscription (trial->active, cancel_at_period_end, etc.)
✔ Multi-DB friendly (SQLite/Postgres)
✔ Observability: log por resultado + log de errores por suscripción
✔ Auditoría v2.1: actor system por negocio (siempre que haya negocio_id)
✔ Idempotente best-effort; en Postgres usa SKIP LOCKED
✔ No toca Inbound/WMS
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from core.logging_config import logger
from core.models.saas import SuscripcionModulo
from core.models.time import utcnow
from core.services.services_subscriptions import renew_subscription


@dataclass
class RenewalJobResult:
    checked: int
    renewed: int
    cancelled: int
    errors: int
    subscription_ids: list[int]


def _status_str(sub: SuscripcionModulo) -> str:
    # Soporta Enum o string
    s = getattr(sub, "status", None)
    return (getattr(s, "value", str(s)) or "").lower()


def _module_str(sub: SuscripcionModulo) -> str:
    mk = getattr(sub, "module_key", None)
    return (getattr(mk, "value", str(mk)) or "").lower()


def _system_actor_for_negocio(negocio_id: int) -> dict:
    # actor compatible con audit(): user dict con negocio_id
    return {"email": "sistema@orbion", "negocio_id": int(negocio_id), "role": "system"}


def run_subscription_renewal_job(
    db: Session,
    *,
    now: Optional[datetime] = None,
    limit: int = 500,
) -> RenewalJobResult:
    """
    Ejecuta renovación para suscripciones que requieren acción.

    - Renueva periodos cuando next_renewal_at <= now
    - Aplica cancel_at_period_end dentro de renew_subscription
    - commit por suscripción para que una falla no tumbe todo el job
    - En Postgres intenta SKIP LOCKED para evitar doble-proceso concurrente
    """
    ts = now or utcnow()

    base_q = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.next_renewal_at.isnot(None))
        .filter(SuscripcionModulo.next_renewal_at <= ts)
        # CANCELLED no entra al job
        .filter(and_(SuscripcionModulo.status.isnot(None)))
        .order_by(SuscripcionModulo.next_renewal_at.asc())
        .limit(limit)
    )

    # Best-effort locking: solo funciona en Postgres. En SQLite, no aplica.
    try:
        subs: List[SuscripcionModulo] = base_q.with_for_update(skip_locked=True).all()
    except Exception:
        subs = base_q.all()

    # Filtrado defensivo por status string (por si DB contiene strings legacy)
    subs = [s for s in subs if _status_str(s) != "cancelled"]

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
            before_status = _status_str(sub)
            before_period_end = getattr(sub, "current_period_end", None)

            actor = _system_actor_for_negocio(int(sub.negocio_id))

            # renew_subscription:
            # - trial->active
            # - mueve período si corresponde
            # - setea next_renewal_at
            # - aplica cancel_at_period_end
            # - audita si actor viene
            renew_subscription(db, sub, actor=actor)

            after_status = _status_str(sub)
            after_period_end = getattr(sub, "current_period_end", None)

            db.commit()

            # Cancelada en este ciclo
            if after_status == "cancelled" and before_status != "cancelled":
                cancelled += 1
                logger.info(
                    "[JOB] subscription cancelled id=%s negocio_id=%s module=%s",
                    sub.id,
                    sub.negocio_id,
                    _module_str(sub),
                )
                continue

            # “Renewed” si cambió el período (o si pasó trial->active con limpieza)
            if after_period_end and before_period_end and after_period_end != before_period_end:
                renewed += 1
                logger.info(
                    "[JOB] subscription renewed id=%s negocio_id=%s module=%s status=%s",
                    sub.id,
                    sub.negocio_id,
                    _module_str(sub),
                    after_status,
                )
            else:
                # Puede ser válido: renew_subscription decidió no mover período (ej: suspended/past_due)
                logger.info(
                    "[JOB] subscription processed (no period change) id=%s negocio_id=%s module=%s status=%s",
                    sub.id,
                    sub.negocio_id,
                    _module_str(sub),
                    after_status,
                )

        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            errors += 1
            logger.exception(
                "[JOB] renew_subscriptions error id=%s negocio_id=%s module=%s err=%s",
                getattr(sub, "id", None),
                getattr(sub, "negocio_id", None),
                _module_str(sub),
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
