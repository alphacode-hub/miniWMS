# core/services/services_subscriptions.py
"""
services_subscriptions.py – ORBION SaaS (enterprise, baseline aligned)

✔ Lifecycle por módulo (activate / renew / cancel / suspend / unschedule)
✔ Trial por módulo
✔ Períodos rolling mensuales (V1: 30 días * meses) [estable multi-db]
✔ Multi-tenant estricto (a nivel de negocio_id; validación puede hacerse en capa superior)
✔ Seguro SQLite / Postgres
✔ Auditoría enterprise v2.1 integrada (source of truth)
✔ Sin acoplar Inbound / WMS
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models.enums import ModuleKey, SubscriptionStatus
from core.models.saas import SuscripcionModulo
from core.models.time import utcnow
from core.services.services_audit import audit, AuditAction


# =========================================================
# CONFIGURACIÓN (V1)
# =========================================================

DEFAULT_TRIAL_DAYS = 14
DEFAULT_BILLING_MONTHS = 1


# =========================================================
# HELPERS INTERNOS
# =========================================================

def _rolling_period(start_at: datetime, months: int = 1) -> Tuple[datetime, datetime]:
    """
    Calcula período rolling mensual.
    V1 simple (baseline): months * 30 días.
    Nota: si más adelante quieres meses calendario reales, lo migramos con cuidado.
    """
    m = int(months or 1)
    if m < 1:
        m = 1
    end_at = start_at + timedelta(days=30 * m)
    return start_at, end_at


def _get_existing_subscription(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
) -> Optional[SuscripcionModulo]:
    return (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == module_key)
        .first()
    )


def _audit_event(
    db: Session,
    *,
    actor: Optional[dict],
    action: AuditAction,
    payload: dict,
) -> None:
    """
    Auditoría enterprise v2.1.
    Nunca rompe flujo de negocio.
    """
    if not actor:
        return
    try:
        audit(
            db=db,
            actor=actor,
            action=action,
            payload=payload,
            commit=False,
        )
    except Exception:
        # "never break business flow"
        return


def _ensure_int_flag(v) -> int:
    try:
        return 1 if int(v) else 0
    except Exception:
        return 0


# =========================================================
# API PÚBLICA (ENTERPRISE)
# =========================================================

def activate_module(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    *,
    start_trial: bool = True,
    trial_days: int = DEFAULT_TRIAL_DAYS,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Activa un módulo para un negocio.

    Reglas:
    - Si existe y está CANCELLED: se "reactiva" (nuevo ciclo) con trial opcional.
    - Si existe y está SUSPENDED/PAST_DUE: se reactiva y limpia past_due_since.
    - Si existe y está ACTIVE/TRIAL: idempotente (retorna tal cual).
    """

    now = utcnow()
    td = max(0, int(trial_days or 0))

    existing = _get_existing_subscription(db, negocio_id, module_key)

    if existing:
        prev_status = existing.status

        if existing.status == SubscriptionStatus.CANCELLED:
            existing.status = SubscriptionStatus.TRIAL if start_trial else SubscriptionStatus.ACTIVE
            existing.started_at = now
            existing.trial_ends_at = (now + timedelta(days=td)) if (start_trial and td > 0) else None

            p_start, p_end = _rolling_period(now, DEFAULT_BILLING_MONTHS)
            existing.current_period_start = p_start
            existing.current_period_end = p_end
            existing.next_renewal_at = p_end

            existing.cancel_at_period_end = 0
            existing.cancelled_at = None
            existing.past_due_since = None

            db.flush()

            _audit_event(
                db,
                actor=actor,
                action=AuditAction.MODULE_ACTIVATE,
                payload={
                    "negocio_id": negocio_id,
                    "module": module_key.value,
                    "from_status": prev_status.value,
                    "to_status": existing.status.value,
                    "trial": bool(existing.trial_ends_at),
                    "reactivated": True,
                },
            )
            return existing

        if existing.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
            existing.status = SubscriptionStatus.TRIAL if start_trial else SubscriptionStatus.ACTIVE
            existing.trial_ends_at = (now + timedelta(days=td)) if (start_trial and td > 0) else None
            existing.past_due_since = None

            # Asegura períodos si faltan
            if not existing.current_period_start or not existing.current_period_end:
                p_start, p_end = _rolling_period(now, DEFAULT_BILLING_MONTHS)
                existing.current_period_start = p_start
                existing.current_period_end = p_end
                existing.next_renewal_at = p_end
            else:
                existing.next_renewal_at = existing.current_period_end

            db.flush()

            _audit_event(
                db,
                actor=actor,
                action=AuditAction.MODULE_ACTIVATE,
                payload={
                    "negocio_id": negocio_id,
                    "module": module_key.value,
                    "from_status": prev_status.value,
                    "to_status": existing.status.value,
                    "reactivated": True,
                    "trial": bool(existing.trial_ends_at),
                },
            )
            return existing

        # ACTIVE/TRIAL: idempotente
        return existing

    status = SubscriptionStatus.TRIAL if start_trial else SubscriptionStatus.ACTIVE
    trial_ends_at = (now + timedelta(days=td)) if (start_trial and td > 0) else None
    p_start, p_end = _rolling_period(now, DEFAULT_BILLING_MONTHS)

    sub = SuscripcionModulo(
        negocio_id=negocio_id,
        module_key=module_key,
        status=status,
        started_at=now,
        trial_ends_at=trial_ends_at,
        current_period_start=p_start,
        current_period_end=p_end,
        next_renewal_at=p_end,
        cancel_at_period_end=0,
    )

    # Savepoint: evita rollback global del request si hay carrera por unique constraint
    try:
        with db.begin_nested():
            db.add(sub)
            db.flush()
    except IntegrityError:
        # No hacemos db.rollback() global.
        # Simplemente recuperamos el registro que ganó la carrera.
        existing2 = _get_existing_subscription(db, negocio_id, module_key)
        if not existing2:
            raise
        return existing2

    _audit_event(
        db,
        actor=actor,
        action=AuditAction.MODULE_ACTIVATE,
        payload={
            "negocio_id": negocio_id,
            "module": module_key.value,
            "status": status.value,
            "trial": bool(trial_ends_at),
            "new": True,
        },
    )
    return sub


def renew_subscription(
    db: Session,
    sub: SuscripcionModulo,
    *,
    months: int = DEFAULT_BILLING_MONTHS,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Renueva período.

    Reglas:
    - CANCELLED: no renueva
    - SUSPENDED/PAST_DUE: no renueva (se reactivan por flujo de pagos / admin)
    - TRIAL expirado -> pasa a ACTIVE antes de renovar
    - Si cancel_at_period_end=1: en renovación se cierra definitivamente (CANCELLED)
    """

    now = utcnow()

    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    if sub.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
        return sub

    if sub.status == SubscriptionStatus.TRIAL and sub.trial_ends_at and sub.trial_ends_at <= now:
        sub.status = SubscriptionStatus.ACTIVE
        sub.trial_ends_at = None

    if _ensure_int_flag(sub.cancel_at_period_end):
        prev = sub.status
        sub.status = SubscriptionStatus.CANCELLED
        sub.cancelled_at = now
        sub.next_renewal_at = None
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_CANCEL_AT_PERIOD_END,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_status": prev.value,
            },
        )
        return sub

    m = max(1, int(months or 1))

    old_end = sub.current_period_end

    if not old_end:
        p_start, p_end = _rolling_period(now, m)
        sub.current_period_start = p_start
        sub.current_period_end = p_end
        sub.next_renewal_at = p_end
        sub.last_payment_at = now
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_RENEW_NOW,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "to_period_end": p_end.isoformat(),
            },
        )
        return sub

    new_start = old_end if old_end > now else now
    new_start, new_end = _rolling_period(new_start, m)

    sub.current_period_start = new_start
    sub.current_period_end = new_end
    sub.next_renewal_at = new_end
    sub.last_payment_at = now

    db.flush()

    _audit_event(
        db,
        actor=actor,
        action=AuditAction.MODULE_RENEW_NOW,
        payload={
            "negocio_id": sub.negocio_id,
            "module": sub.module_key.value,
            "from_period_end": old_end.isoformat() if old_end else None,
            "to_period_end": new_end.isoformat(),
        },
    )
    return sub


def cancel_subscription_at_period_end(
    db: Session,
    sub: SuscripcionModulo,
    *,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Agenda cancelación al fin del período actual.
    (No cancela inmediatamente).
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    sub.cancel_at_period_end = 1
    db.flush()

    _audit_event(
        db,
        actor=actor,
        action=AuditAction.MODULE_CANCEL_AT_PERIOD_END,
        payload={
            "negocio_id": sub.negocio_id,
            "module": sub.module_key.value,
        },
    )
    return sub


def unschedule_cancel(
    db: Session,
    sub: SuscripcionModulo,
    *,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Quita cancelación agendada.
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    sub.cancel_at_period_end = 0
    db.flush()

    _audit_event(
        db,
        actor=actor,
        action=AuditAction.MODULE_UNSCHEDULE_CANCEL,
        payload={
            "negocio_id": sub.negocio_id,
            "module": sub.module_key.value,
        },
    )
    return sub


def suspend_subscription(
    db: Session,
    sub: SuscripcionModulo,
    *,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Suspende (por no pago / fraude / admin).
    No aplica si ya está CANCELLED.
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    if sub.status != SubscriptionStatus.SUSPENDED:
        prev = sub.status
        sub.status = SubscriptionStatus.SUSPENDED

        # Marca "desde cuándo" está en problema (si quieres separar SUSPENDED vs PAST_DUE, lo ajustamos)
        sub.past_due_since = sub.past_due_since or utcnow()

        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_SUSPEND,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_status": prev.value,
            },
        )
    return sub
