# core/services/services_usage.py
"""
services_usage.py – ORBION SaaS (enterprise, baseline aligned)

✔ Usage por periodo operacional (mes calendario America/Santiago -> persistido en UTC)
✔ Scope: negocio_id + module_key + counter_type + metric_key + periodo
✔ Seguro multi-db (SQLite / Postgres)
✔ Resiliente ante concurrencia (UniqueConstraint + IntegrityError + retry)
✔ Incremento atómico (evita lost update)
✔ Strategy C: OPERATIONAL + BILLABLE (por el mismo evento)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models.enums import ModuleKey, UsageCounterType, SubscriptionStatus
from core.models.saas import UsageCounter, SuscripcionModulo
from core.models.time import utcnow
 


try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_CL_TZ = ZoneInfo("America/Santiago") if ZoneInfo else None


# =========================================================
# TIPOS
# =========================================================

@dataclass(frozen=True)
class UsageWindow:
    period_start: datetime
    period_end: datetime


# =========================================================
# HELPERS
# =========================================================

def _ensure_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_metric_key(metric_key: str) -> str:
    return (metric_key or "").strip().lower()


def _operational_month_window(now_utc: datetime | None = None) -> UsageWindow:
    """
    Ventana: mes calendario en America/Santiago, persistido como UTC tz-aware.
    Ej: enero = [2026-01-01 00:00 CL, 2026-02-01 00:00 CL)
    """
    now = now_utc or utcnow()
    now = _ensure_utc_aware(now)

    if not _CL_TZ:
        # fallback: mes UTC
        y, m = now.year, now.month
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        if m == 12:
            end = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(y, m + 1, 1, tzinfo=timezone.utc)
        return UsageWindow(period_start=start, period_end=end)

    now_cl = now.astimezone(_CL_TZ)
    y, m = now_cl.year, now_cl.month

    start_cl = datetime(y, m, 1, 0, 0, 0, tzinfo=_CL_TZ)
    if m == 12:
        end_cl = datetime(y + 1, 1, 1, 0, 0, 0, tzinfo=_CL_TZ)
    else:
        end_cl = datetime(y, m + 1, 1, 0, 0, 0, tzinfo=_CL_TZ)

    # persistimos como UTC
    return UsageWindow(
        period_start=_ensure_utc_aware(start_cl),
        period_end=_ensure_utc_aware(end_cl),
    )


def _subscription_period_window(
    db: Session,
    *,
    negocio_id: int,
    module_key: ModuleKey,
    now_utc: datetime | None = None,
) -> UsageWindow | None:
    """
    Ventana BILLABLE por módulo:
    - Solo si la suscripción está ACTIVE y tiene current_period_start/end.
    - Retorna None si no aplica (trial/past_due/suspended/cancelled o campos faltantes).
    """
    now = now_utc or utcnow()
    now = _ensure_utc_aware(now)

    sub = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == int(negocio_id))
        .filter(SuscripcionModulo.module_key == module_key)
        .first()
    )

    if not sub:
        return None

    st = getattr(sub.status, "value", str(sub.status)).lower().strip()
    if st != SubscriptionStatus.ACTIVE.value:
        return None

    ps = getattr(sub, "current_period_start", None)
    pe = getattr(sub, "current_period_end", None)
    if not ps or not pe:
        return None

    ps = _ensure_utc_aware(ps)
    pe = _ensure_utc_aware(pe)
    if pe <= ps:
        return None

    return UsageWindow(period_start=ps, period_end=pe)

def _resolve_window(
    db: Session,
    *,
    negocio_id: int,
    module_key: ModuleKey,
    counter_type: UsageCounterType,
) -> UsageWindow:
    # OPERATIONAL = mes calendario CL (analítica)
    if counter_type == UsageCounterType.OPERATIONAL:
        return _operational_month_window()

    # BILLABLE = período de suscripción si existe; si no, fallback mensual CL
    w = _subscription_period_window(db, negocio_id=negocio_id, module_key=module_key)
    return w or _operational_month_window()



def _get_or_create_counter(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    counter_type: UsageCounterType,
    metric_key: str,
    window: UsageWindow,
) -> UsageCounter:
    metric = _norm_metric_key(metric_key)
    if not metric:
        raise ValueError("metric_key vacío no es válido")

    def _query():
        return (
            db.query(UsageCounter)
            .filter(UsageCounter.negocio_id == negocio_id)
            .filter(UsageCounter.module_key == module_key)
            .filter(UsageCounter.counter_type == counter_type)
            .filter(UsageCounter.metric_key == metric)
            .filter(UsageCounter.period_start == window.period_start)
            .filter(UsageCounter.period_end == window.period_end)
        )

    row = _query().first()
    if row:
        return row

    for _ in range(2):
        try:
            with db.begin_nested():
                row_new = UsageCounter(
                    negocio_id=negocio_id,
                    module_key=module_key,
                    counter_type=counter_type,
                    metric_key=metric,
                    period_start=window.period_start,
                    period_end=window.period_end,
                    value=0.0,
                )
                db.add(row_new)
                db.flush()
                return row_new
        except IntegrityError:
            row2 = _query().first()
            if row2:
                return row2
            continue

    raise IntegrityError(
        statement=None,
        params=None,
        orig=Exception("No se pudo crear/obtener UsageCounter tras colisión."),
    )


# =========================================================
# API PÚBLICA
# =========================================================

def get_usage_value(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
    *,
    counter_type: UsageCounterType = UsageCounterType.BILLABLE,
) -> float:
    window = _resolve_window(
        db,
        negocio_id=negocio_id,
        module_key=module_key,
        counter_type=counter_type,
    )

    metric = _norm_metric_key(metric_key)
    if not metric:
        return 0.0

    row = (
        db.query(UsageCounter.value)
        .filter(UsageCounter.negocio_id == negocio_id)
        .filter(UsageCounter.module_key == module_key)
        .filter(UsageCounter.counter_type == counter_type)
        .filter(UsageCounter.metric_key == metric)
        .filter(UsageCounter.period_start == window.period_start)
        .filter(UsageCounter.period_end == window.period_end)
        .first()
    )
    return float(row[0]) if row and row[0] is not None else 0.0


def increment_usage(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
    delta: float = 1.0,
    *,
    counter_type: UsageCounterType = UsageCounterType.BILLABLE,
) -> float:
    try:
        d = float(delta)
    except Exception:
        d = 0.0

    if d <= 0:
        return get_usage_value(db, negocio_id, module_key, metric_key, counter_type=counter_type)

    window = _resolve_window(
        db,
        negocio_id=negocio_id,
        module_key=module_key,
        counter_type=counter_type,
    )

    metric = _norm_metric_key(metric_key)
    if not metric:
        return 0.0

    row = _get_or_create_counter(db, negocio_id, module_key, counter_type, metric, window)

    db.execute(
        update(UsageCounter)
        .where(UsageCounter.id == row.id)
        .values(
            value=UsageCounter.value + d,
            updated_at=utcnow(),
        )
    )
    db.flush()

    row2 = db.query(UsageCounter.value).filter(UsageCounter.id == row.id).first()
    return float(row2[0]) if row2 and row2[0] is not None else float(d)


def increment_usage_dual(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
    delta: float = 1.0,
) -> tuple[float, float]:
    """
    Strategy C: incrementa OPERATIONAL y BILLABLE por el mismo evento.
    Retorna (operational_value, billable_value).
    """
    op = increment_usage(
        db,
        negocio_id,
        module_key,
        metric_key,
        delta,
        counter_type=UsageCounterType.OPERATIONAL,
    )
    bill = increment_usage(
        db,
        negocio_id,
        module_key,
        metric_key,
        delta,
        counter_type=UsageCounterType.BILLABLE,
    )
    return op, bill


def list_usage_for_module_current_period(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    *,
    counter_type: UsageCounterType = UsageCounterType.BILLABLE,
) -> dict[str, float]:
    window = _resolve_window(
        db,
        negocio_id=negocio_id,
        module_key=module_key,
        counter_type=counter_type,
    )


    rows = (
        db.query(UsageCounter.metric_key, UsageCounter.value)
        .filter(UsageCounter.negocio_id == negocio_id)
        .filter(UsageCounter.module_key == module_key)
        .filter(UsageCounter.counter_type == counter_type)
        .filter(UsageCounter.period_start == window.period_start)
        .filter(UsageCounter.period_end == window.period_end)
        .all()
    )

    out: dict[str, float] = {}
    for k, v in rows:
        out[str(k)] = float(v or 0.0)
    return out
