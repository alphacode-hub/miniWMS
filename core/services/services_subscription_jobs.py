# core/services/services_subscription_jobs.py
"""
services_subscription_jobs.py – ORBION SaaS (enterprise, baseline aligned)

Job V1 (Contrato):
- TRIAL: NO tiene período comercial (current_period_* = None, next_renewal_at = None)
- ACTIVE: TIENE período comercial + next_renewal_at = current_period_end
- TRIAL expirado -> CANCELLED
- ACTIVE vencido sin pago -> PAST_DUE (cobranzas), NO renueva
- cancel_at_period_end=1 -> CANCELLED al corte (en renew_subscription)

Notas:
- "Pago real" aún no existe: usamos heurística con last_payment_at.
  Si last_payment_at >= current_period_start => asumimos pagado.
  (Cuando tengas webhooks, reemplazas por señal real.)
- Multi-DB friendly (SQLite/Postgres)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import or_

from core.logging_config import logger
from core.models.enums import SubscriptionStatus
from core.models.saas import SuscripcionModulo
from core.models.time import utcnow

from core.services.services_subscriptions import renew_subscription


# =========================================================
# CONFIG
# =========================================================

DEFAULT_BATCH_SIZE = 200
DEFAULT_LOOKAHEAD_MINUTES = 5  # ventana para agarrar “lo que está por vencer”


# =========================================================
# RESULT
# =========================================================

@dataclass
class SubscriptionJobCounters:
    scanned: int = 0
    renewed: int = 0
    past_due: int = 0
    cancelled: int = 0
    trial_expired: int = 0
    repaired: int = 0
    skipped: int = 0
    errors: int = 0


def _as_iso(dt: Optional[datetime]) -> Optional[str]:
    try:
        return dt.isoformat() if dt else None
    except Exception:
        return None


def _system_actor_for_negocio(negocio_id: int) -> dict:
    # actor compatible con audit(): user dict con negocio_id
    return {"email": "sistema@orbion", "negocio_id": int(negocio_id), "role": "system"}


def _q_due(db: Session, *, now: datetime, lookahead_minutes: int, limit: int) -> List[SuscripcionModulo]:
    horizon = now + timedelta(minutes=max(0, int(lookahead_minutes or 0)))

    # Procesamos:
    # - paid: next_renewal_at <= horizon
    # - trials: status == TRIAL (aunque no tengan cola)
    return (
        db.query(SuscripcionModulo)
        .filter(
            or_(
                (SuscripcionModulo.next_renewal_at.isnot(None) & (SuscripcionModulo.next_renewal_at <= horizon)),
                (SuscripcionModulo.status == SubscriptionStatus.TRIAL),
            )
        )
        .order_by(SuscripcionModulo.id.asc())
        .limit(int(limit or DEFAULT_BATCH_SIZE))
        .all()
    )


def _mark_past_due(db: Session, sub: SuscripcionModulo, *, now: datetime) -> None:
    """
    Marca PAST_DUE (cobranzas).
    Contrato v1:
    - Mantiene el período vencido tal cual (para referencia).
    - Apaga la cola (next_renewal_at = None) para no reprocesar infinito.
      (Cuando el cliente pague, flujo de pago debe reactivar/renovar).
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return

    if sub.status != SubscriptionStatus.PAST_DUE:
        sub.status = SubscriptionStatus.PAST_DUE

    sub.past_due_since = sub.past_due_since or now
    sub.next_renewal_at = None
    db.flush()


def run_subscriptions_job(
    db: Session,
    *,
    now: Optional[datetime] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lookahead_minutes: int = DEFAULT_LOOKAHEAD_MINUTES,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Ejecuta 1 ciclo del job.
    """
    ts = now or utcnow()
    counters = SubscriptionJobCounters()

    subs = _q_due(db, now=ts, lookahead_minutes=lookahead_minutes, limit=batch_size)

    logger.info(
        "[JOB] subscriptions_job start now=%s batch=%s lookahead_min=%s found=%s",
        _as_iso(ts),
        batch_size,
        lookahead_minutes,
        len(subs),
    )

    for sub in subs:
        counters.scanned += 1
        try:
            st = sub.status

            # -------------------------
            # TRIAL: expira -> CANCELLED
            # -------------------------
            if st == SubscriptionStatus.TRIAL:
                te = sub.trial_ends_at
                if te and te <= ts:
                    sub.status = SubscriptionStatus.CANCELLED
                    sub.cancelled_at = ts

                    # contrato v1: trial no tiene período
                    sub.trial_ends_at = None
                    sub.current_period_start = None
                    sub.current_period_end = None
                    sub.next_renewal_at = None
                    sub.cancel_at_period_end = 0

                    db.flush()
                    counters.trial_expired += 1
                    counters.cancelled += 1

                    logger.info(
                        "[JOB] trial expired -> cancelled sub_id=%s negocio_id=%s module=%s",
                        sub.id, sub.negocio_id, getattr(sub.module_key, "value", str(sub.module_key))
                    )
                else:
                    counters.skipped += 1
                continue

            # -------------------------
            # No procesables
            # -------------------------
            if st in (SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
                counters.skipped += 1
                continue

            # -------------------------
            # ACTIVE: revisar vencimiento
            # -------------------------
            if st == SubscriptionStatus.ACTIVE:
                end = sub.current_period_end

                # Repair: ACTIVE sin período => lo reparamos renovando 1 mes (rolling)
                if not end or not sub.current_period_start:
                    actor = _system_actor_for_negocio(int(sub.negocio_id))
                    renew_subscription(db, sub, months=1, actor=actor)
                    if sub.current_period_end:
                        sub.next_renewal_at = sub.current_period_end
                    db.flush()
                    counters.repaired += 1
                    continue

                # Si aún no vence
                if end > ts:
                    counters.skipped += 1
                    continue

                # Venció: si cancel_at_period_end -> renew_subscription lo cancela
                if int(sub.cancel_at_period_end or 0) == 1:
                    actor = _system_actor_for_negocio(int(sub.negocio_id))
                    renew_subscription(db, sub, months=1, actor=actor)
                    db.flush()

                    if sub.status == SubscriptionStatus.CANCELLED:
                        counters.cancelled += 1
                    else:
                        counters.renewed += 1
                    continue

                # Heurística “pago OK”
                paid_ok = False
                try:
                    if sub.last_payment_at and sub.current_period_start:
                        paid_ok = sub.last_payment_at >= sub.current_period_start
                except Exception:
                    paid_ok = False

                if paid_ok:
                    actor = _system_actor_for_negocio(int(sub.negocio_id))
                    renew_subscription(db, sub, months=1, actor=actor)
                    if sub.current_period_end:
                        sub.next_renewal_at = sub.current_period_end
                    db.flush()
                    counters.renewed += 1
                else:
                    _mark_past_due(db, sub, now=ts)
                    counters.past_due += 1

                continue

            counters.skipped += 1

        except Exception as e:
            counters.errors += 1
            logger.exception(
                "[JOB] subscriptions_job error sub_id=%s negocio_id=%s err=%s",
                getattr(sub, "id", None),
                getattr(sub, "negocio_id", None),
                str(e),
            )

    if commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise

    logger.info(
        "[JOB] subscriptions_job end scanned=%s renewed=%s past_due=%s cancelled=%s trial_expired=%s repaired=%s skipped=%s errors=%s",
        counters.scanned,
        counters.renewed,
        counters.past_due,
        counters.cancelled,
        counters.trial_expired,
        counters.repaired,
        counters.skipped,
        counters.errors,
    )

    return {
        "ok": True,
        "now": _as_iso(ts),
        "batch_size": int(batch_size or DEFAULT_BATCH_SIZE),
        "lookahead_minutes": int(lookahead_minutes or DEFAULT_LOOKAHEAD_MINUTES),
        "counters": counters.__dict__,
    }
