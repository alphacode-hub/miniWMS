# core/services/services_subscriptions.py
"""
services_subscriptions.py – ORBION SaaS (enterprise)

✔ Lifecycle por módulo (activate / renew / cancel)
✔ Trial por módulo
✔ Períodos rolling mensuales
✔ Multi-tenant estricto
✔ Seguro SQLite / Postgres
✔ Sin acoplar Inbound / WMS
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.models.saas import SuscripcionModulo
from core.models.enums import ModuleKey, SubscriptionStatus
from core.models.time import utcnow


# =========================================================
# CONFIGURACIÓN (V1)
# =========================================================

DEFAULT_TRIAL_DAYS = 14
DEFAULT_BILLING_MONTHS = 1


# =========================================================
# HELPERS
# =========================================================

def _rolling_period(start_at: datetime, months: int = 1) -> Tuple[datetime, datetime]:
    """
    Calcula período rolling mensual.

    V1 simple: months * 30 días.
    (Se puede reemplazar por dateutil.relativedelta si quieres calendario exacto.)
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


# =========================================================
# API PUBLICA
# =========================================================

def activate_module(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    *,
    start_trial: bool = True,
    trial_days: int = DEFAULT_TRIAL_DAYS,
) -> SuscripcionModulo:
    """
    Activa un módulo para un negocio.
    - Si no existe: crea suscripción (trial o active)
    - Si existe y está cancelled: reactiva (misma fila) creando nuevo período
    - Si existe y está suspended/past_due: reactiva (política V1)
    """

    now = utcnow()

    td = int(trial_days or 0)
    if td < 0:
        td = 0

    existing = _get_existing_subscription(db, negocio_id, module_key)

    if existing:
        # 1) Si está cancelada: reactivar (misma fila) con nuevo período
        if existing.status == SubscriptionStatus.CANCELLED:
            existing.status = SubscriptionStatus.TRIAL if start_trial else SubscriptionStatus.ACTIVE
            existing.started_at = now
            existing.trial_ends_at = (now + timedelta(days=td)) if start_trial and td > 0 else None

            p_start, p_end = _rolling_period(now, DEFAULT_BILLING_MONTHS)
            existing.current_period_start = p_start
            existing.current_period_end = p_end
            existing.next_renewal_at = p_end

            existing.cancel_at_period_end = 0
            existing.cancelled_at = None
            existing.past_due_since = None  # ✅ limpiar trazas de deuda/suspensión

            db.flush()
            return existing

        # 2) Si está suspendida o past_due: reactivar (política enterprise conservadora)
        if existing.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
            existing.status = SubscriptionStatus.TRIAL if start_trial else SubscriptionStatus.ACTIVE
            existing.trial_ends_at = (now + timedelta(days=td)) if start_trial and td > 0 else None
            existing.past_due_since = None

            # Si no tiene periodo vigente, creamos uno; si tiene, lo respetamos.
            if not existing.current_period_start or not existing.current_period_end:
                p_start, p_end = _rolling_period(now, DEFAULT_BILLING_MONTHS)
                existing.current_period_start = p_start
                existing.current_period_end = p_end
                existing.next_renewal_at = p_end
            else:
                # asegurar que next_renewal_at exista
                existing.next_renewal_at = existing.current_period_end

            db.flush()
            return existing

        # 3) Ya existe y está operativa → no duplicar
        return existing

    # Crear nueva suscripción
    status = SubscriptionStatus.TRIAL if start_trial else SubscriptionStatus.ACTIVE
    trial_ends_at = (now + timedelta(days=td)) if start_trial and td > 0 else None

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
    )

    db.add(sub)
    try:
        db.flush()
        return sub
    except IntegrityError:
        db.rollback()
        # Otro proceso la creó
        sub2 = _get_existing_subscription(db, negocio_id, module_key)
        if not sub2:
            raise
        return sub2


def renew_subscription(
    db: Session,
    sub: SuscripcionModulo,
    *,
    months: int = DEFAULT_BILLING_MONTHS,
) -> SuscripcionModulo:
    """
    Renueva el período de una suscripción.

    Regla enterprise:
    - Si cancel_at_period_end=1, NO se crea un nuevo período: se marca CANCELLED
      al momento de renovación (next_renewal_at <= now).

    Política V1 conservadora:
    - Si status es SUSPENDED o PAST_DUE, NO renueva automáticamente.
      (Se reactiva vía activate_module o flujo de pago/billing.)
    """
    now = utcnow()

    # Si ya está cancelada, no hacemos nada
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    # Si está suspendida / past_due, por defecto no renovar
    if sub.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
        return sub

    # Si estaba en trial y venció, pasa a active (solo si NO se va a cancelar)
    if sub.status == SubscriptionStatus.TRIAL and sub.trial_ends_at and sub.trial_ends_at <= now:
        sub.status = SubscriptionStatus.ACTIVE
        sub.trial_ends_at = None

    # ✅ Cancelación diferida
    if sub.cancel_at_period_end:
        sub.status = SubscriptionStatus.CANCELLED
        sub.cancelled_at = now
        sub.next_renewal_at = None
        db.flush()
        return sub

    # =========================
    # Renovar período normal
    # =========================
    m = int(months or 1)
    if m < 1:
        m = 1

    if not sub.current_period_end:
        # fallback defensivo
        p_start, p_end = _rolling_period(now, m)
        sub.current_period_start = p_start
        sub.current_period_end = p_end
        sub.next_renewal_at = p_end
        sub.last_payment_at = now
        db.flush()
        return sub

    new_start = sub.current_period_end
    new_start = new_start if new_start > now else now
    new_start, new_end = _rolling_period(new_start, m)

    sub.current_period_start = new_start
    sub.current_period_end = new_end
    sub.next_renewal_at = new_end
    sub.last_payment_at = now

    db.flush()
    return sub


def cancel_subscription_at_period_end(
    db: Session,
    sub: SuscripcionModulo,
) -> SuscripcionModulo:
    """
    Marca una suscripción para cancelarse al final del período actual.
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    sub.cancel_at_period_end = 1
    db.flush()
    return sub


def suspend_subscription(
    db: Session,
    sub: SuscripcionModulo,
) -> SuscripcionModulo:
    """
    Suspende una suscripción (ej: por pago fallido).
    """
    if sub.status != SubscriptionStatus.CANCELLED:
        sub.status = SubscriptionStatus.SUSPENDED
        sub.past_due_since = utcnow()
        db.flush()
    return sub


def unschedule_cancel(db: Session, sub: SuscripcionModulo) -> SuscripcionModulo:
    """
    Revierte una cancelación programada (cancel_at_period_end=0).
    No revive CANCELLED (eso es activate_module / flujo comercial).
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    sub.cancel_at_period_end = 0
    db.flush()
    return sub
