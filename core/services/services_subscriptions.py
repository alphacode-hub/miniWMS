# core/services/services_subscriptions.py
"""
services_subscriptions.py – ORBION SaaS (enterprise, baseline aligned)

✔ Lifecycle por módulo (activate / renew / cancel / suspend / unschedule)
✔ Trial por módulo (CONTRATO V1: trial != paid period)
✔ Períodos rolling mensuales (V1: 30 días * meses) [estable multi-db]
✔ Multi-tenant estricto (a nivel de negocio_id; validación puede hacerse en capa superior)
✔ Seguro SQLite / Postgres
✔ Auditoría enterprise v2.1 integrada (source of truth)
✔ Sin acoplar Inbound / WMS

=========================================================
CONTRATO V1 (importante)
=========================================================
- TRIAL:
  - status=TRIAL
  - trial_ends_at != None
  - current_period_start/end = None
  - next_renewal_at = None
  - last_payment_at = None

- PAID (ACTIVE / PAST_DUE / SUSPENDED):
  - trial_ends_at = None
  - current_period_start/end != None (si ACTIVE/PAST_DUE; en SUSPENDED lo mantenemos)
  - next_renewal_at = current_period_end (cuando corresponde)

- CANCELLED:
  - sin acceso (enabled=False)
  - puede mantener historial, pero next_renewal_at=None

Nota:
- La transición TRIAL -> ACTIVE NO ocurre por renew().
  Debe ocurrir por evento de pago (mark_paid_now()).
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
        # never break business flow
        return


def _ensure_int_flag(v) -> int:
    try:
        return 1 if int(v) else 0
    except Exception:
        return 0


def _set_trial_fields(sub: SuscripcionModulo, *, now: datetime, trial_days: int) -> None:
    """
    Contrato V1: Trial NO crea período.
    """
    td = max(0, int(trial_days or 0))
    sub.status = SubscriptionStatus.TRIAL
    sub.trial_ends_at = (now + timedelta(days=td)) if td > 0 else None

    sub.current_period_start = None
    sub.current_period_end = None
    sub.next_renewal_at = None
    sub.last_payment_at = None
    sub.past_due_since = None


def _set_paid_period_fields(
    sub: SuscripcionModulo,
    *,
    now: datetime,
    months: int = DEFAULT_BILLING_MONTHS,
) -> None:
    """
    Contrato V1: Paid crea período y elimina trial.
    """
    m = max(1, int(months or 1))
    p_start, p_end = _rolling_period(now, m)

    sub.trial_ends_at = None

    sub.status = SubscriptionStatus.ACTIVE
    sub.current_period_start = p_start
    sub.current_period_end = p_end
    sub.next_renewal_at = p_end
    sub.last_payment_at = now
    sub.past_due_since = None


def _clear_cancellation_flags(sub: SuscripcionModulo) -> None:
    sub.cancel_at_period_end = 0
    sub.cancelled_at = None


def _now_iso(x: Optional[datetime]) -> Optional[str]:
    try:
        return x.isoformat() if x else None
    except Exception:
        return None


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

    Reglas (Contrato V1):
    - start_trial=True  -> crea/actualiza suscripción en TRIAL (SIN período).
    - start_trial=False -> crea/actualiza suscripción en ACTIVE (CON período).
    - Si existe y está CANCELLED: se reactiva (nuevo ciclo).
    - Si existe y está SUSPENDED/PAST_DUE: se reactiva (trial o paid) y limpia past_due_since.
    - Si existe y está ACTIVE/TRIAL: idempotente (retorna tal cual).
    """
    now = utcnow()
    td = max(0, int(trial_days or 0))

    existing = _get_existing_subscription(db, negocio_id, module_key)
    if existing:
        prev_status = existing.status

        # CANCELLED -> reactivar
        if existing.status == SubscriptionStatus.CANCELLED:
            existing.started_at = now
            _clear_cancellation_flags(existing)
            existing.past_due_since = None

            if start_trial:
                _set_trial_fields(existing, now=now, trial_days=td)
            else:
                _set_paid_period_fields(existing, now=now, months=DEFAULT_BILLING_MONTHS)

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
                    "period_end": _now_iso(existing.current_period_end),
                    "trial_ends": _now_iso(existing.trial_ends_at),
                },
            )
            return existing

        # SUSPENDED / PAST_DUE -> reactivar
        if existing.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
            _clear_cancellation_flags(existing)
            existing.past_due_since = None

            if start_trial:
                _set_trial_fields(existing, now=now, trial_days=td)
            else:
                _set_paid_period_fields(existing, now=now, months=DEFAULT_BILLING_MONTHS)

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
                    "period_end": _now_iso(existing.current_period_end),
                    "trial_ends": _now_iso(existing.trial_ends_at),
                },
            )
            return existing

        # ACTIVE/TRIAL: idempotente
        return existing

    # Crear nueva
    sub = SuscripcionModulo(
        negocio_id=negocio_id,
        module_key=module_key,
        started_at=now,
        cancel_at_period_end=0,
    )

    if start_trial:
        _set_trial_fields(sub, now=now, trial_days=td)
    else:
        _set_paid_period_fields(sub, now=now, months=DEFAULT_BILLING_MONTHS)

    # Savepoint: evita rollback global del request si hay carrera por unique constraint
    try:
        with db.begin_nested():
            db.add(sub)
            db.flush()
    except IntegrityError:
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
            "status": sub.status.value,
            "trial": bool(sub.trial_ends_at),
            "new": True,
            "period_end": _now_iso(sub.current_period_end),
            "trial_ends": _now_iso(sub.trial_ends_at),
        },
    )
    return sub


def mark_paid_now(
    db: Session,
    sub: SuscripcionModulo,
    *,
    months: int = DEFAULT_BILLING_MONTHS,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Evento de pago (manual o futuro webhook).
    Convierte TRIAL/PAST_DUE/SUSPENDED -> ACTIVE y crea período pagado.

    Contrato V1:
    - Aquí ocurre el salto TRIAL -> ACTIVE.
    - Si ya está ACTIVE: renueva/agrega período desde ahora o desde current_end.
    """
    now = utcnow()
    m = max(1, int(months or 1))

    if sub.status == SubscriptionStatus.CANCELLED:
        # Rehabilitar como paid nuevo ciclo
        prev = sub.status
        sub.started_at = now
        _clear_cancellation_flags(sub)
        _set_paid_period_fields(sub, now=now, months=m)
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_RENEW_NOW,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_status": prev.value,
                "to_status": sub.status.value,
                "paid_event": True,
                "to_period_end": _now_iso(sub.current_period_end),
            },
        )
        return sub

    prev_status = sub.status

    # Si estaba en trial/past_due/suspended, lo llevamos a ACTIVE con período desde ahora
    if sub.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED):
        _clear_cancellation_flags(sub)
        _set_paid_period_fields(sub, now=now, months=m)
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_RENEW_NOW,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_status": prev_status.value,
                "to_status": sub.status.value,
                "paid_event": True,
                "to_period_end": _now_iso(sub.current_period_end),
            },
        )
        return sub

    # ACTIVE: equivale a “compró más meses” => extendemos desde end actual si está en futuro
    if sub.status == SubscriptionStatus.ACTIVE:
        old_end = sub.current_period_end
        base_start = old_end if (old_end and old_end > now) else now
        new_start, new_end = _rolling_period(base_start, m)

        sub.trial_ends_at = None
        sub.current_period_start = new_start
        sub.current_period_end = new_end
        sub.next_renewal_at = new_end
        sub.last_payment_at = now
        sub.past_due_since = None
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_RENEW_NOW,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_period_end": _now_iso(old_end),
                "to_period_end": _now_iso(new_end),
                "paid_event": True,
            },
        )
        return sub

    return sub


def renew_subscription(
    db: Session,
    sub: SuscripcionModulo,
    *,
    months: int = DEFAULT_BILLING_MONTHS,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Renueva período (JOB / cron interno).

    Contrato V1:
    - TRIAL: NO se renueva automáticamente. Si expiró, se cancela (CANCELLED) y se limpian campos.
    - CANCELLED: no renueva.
    - SUSPENDED/PAST_DUE: no renueva (se resuelve por pago/admin).
    - ACTIVE: renueva/avanza período.
    - cancel_at_period_end=1: en la “renovación” se cierra definitivamente (CANCELLED).

    Nota: el “pago” no lo simula este método; eso lo hace mark_paid_now().
    """
    now = utcnow()

    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    if sub.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
        return sub

    # TRIAL: expira => CANCELLED (sin período)
    if sub.status == SubscriptionStatus.TRIAL:
        if sub.trial_ends_at and sub.trial_ends_at <= now:
            prev = sub.status
            sub.status = SubscriptionStatus.CANCELLED
            sub.cancelled_at = now
            sub.trial_ends_at = None
            sub.next_renewal_at = None
            sub.current_period_start = None
            sub.current_period_end = None
            db.flush()

            _audit_event(
                db,
                actor=actor,
                action=AuditAction.MODULE_SUSPEND,  # si tienes un action específico "TRIAL_EXPIRED", cámbialo aquí
                payload={
                    "negocio_id": sub.negocio_id,
                    "module": sub.module_key.value,
                    "from_status": prev.value,
                    "trial_expired": True,
                },
            )
        return sub

    # ACTIVE (paid)
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
        # Caso raro: ACTIVE sin período -> re-crear período desde now
        _set_paid_period_fields(sub, now=now, months=m)
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_RENEW_NOW,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "to_period_end": _now_iso(sub.current_period_end),
                "repair_missing_period": True,
            },
        )
        return sub

    # Rolling: si el end era futuro, renueva desde old_end; si ya pasó, desde now
    base_start = old_end if old_end > now else now
    new_start, new_end = _rolling_period(base_start, m)

    sub.trial_ends_at = None
    sub.status = SubscriptionStatus.ACTIVE
    sub.current_period_start = new_start
    sub.current_period_end = new_end
    sub.next_renewal_at = new_end
    sub.last_payment_at = now
    sub.past_due_since = None

    db.flush()

    _audit_event(
        db,
        actor=actor,
        action=AuditAction.MODULE_RENEW_NOW,
        payload={
            "negocio_id": sub.negocio_id,
            "module": sub.module_key.value,
            "from_period_end": _now_iso(old_end),
            "to_period_end": _now_iso(new_end),
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

    Nota: Si está en TRIAL, la cancelación “al fin de período” no aplica.
    Puedes:
      - cancelar inmediatamente (CANCELLED), o
      - marcar cancel_at_period_end igualmente, pero no hay período.
    En V1 conservador: si está en TRIAL -> CANCELLED inmediato.
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    now = utcnow()

    if sub.status == SubscriptionStatus.TRIAL:
        prev = sub.status
        sub.status = SubscriptionStatus.CANCELLED
        sub.cancelled_at = now
        sub.trial_ends_at = None
        sub.next_renewal_at = None
        sub.current_period_start = None
        sub.current_period_end = None
        sub.cancel_at_period_end = 0
        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_CANCEL_AT_PERIOD_END,
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_status": prev.value,
                "cancelled_from_trial": True,
            },
        )
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

    Nota V1: SUSPENDED corta acceso (enabled=False) si tu propiedad enabled
    solo considera TRIAL/ACTIVE. (En tu modelo enabled está TRIAL/ACTIVE).
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    if sub.status != SubscriptionStatus.SUSPENDED:
        prev = sub.status
        sub.status = SubscriptionStatus.SUSPENDED
        sub.trial_ends_at = None  # contrato v1: suspended no es trial
        sub.past_due_since = sub.past_due_since or utcnow()
        sub.next_renewal_at = None  # opcional: lo sacamos de la cola de renovación automática

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


def mark_past_due(
    db: Session,
    sub: SuscripcionModulo,
    *,
    actor: Optional[dict] = None,
) -> SuscripcionModulo:
    """
    Marca PAST_DUE (no pago al renovar).
    Útil para jobs: si llegó current_period_end y no hay pago confirmado.
    """
    if sub.status == SubscriptionStatus.CANCELLED:
        return sub

    if sub.status != SubscriptionStatus.PAST_DUE:
        prev = sub.status
        sub.status = SubscriptionStatus.PAST_DUE
        sub.trial_ends_at = None
        sub.past_due_since = sub.past_due_since or utcnow()
        sub.next_renewal_at = None

        db.flush()

        _audit_event(
            db,
            actor=actor,
            action=AuditAction.MODULE_SUSPEND,  # si tienes un action específico PAST_DUE, cámbialo aquí
            payload={
                "negocio_id": sub.negocio_id,
                "module": sub.module_key.value,
                "from_status": prev.value,
                "past_due": True,
            },
        )

    return sub
