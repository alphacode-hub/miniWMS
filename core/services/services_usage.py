# core/services/services_usage.py
"""
services_usage.py – ORBION SaaS (enterprise)

✔ Usage no acumulativo por período (period_start/end)
✔ Scope: negocio_id + module_key + metric_key + periodo
✔ Seguro multi-db (SQLite / Postgres)
✔ Resiliente ante concurrencia (UniqueConstraint + IntegrityError + retry)
✔ No acopla módulos funcionales (Inbound/WMS) con planes/billing
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.models.saas import UsageCounter, SuscripcionModulo
from core.models.enums import ModuleKey
from core.models.time import utcnow


# =========================================================
# TIPOS
# =========================================================

@dataclass(frozen=True)
class UsageWindow:
    """
    Ventana de uso (mismo periodo que la suscripción del módulo).
    """
    period_start: datetime
    period_end: datetime


# =========================================================
# HELPERS
# =========================================================

def _ensure_tz(dt: datetime) -> datetime:
    """
    Asegura timezone-aware. En ORBION usamos UTC tz-aware.
    """
    if dt.tzinfo is None:
        # asumimos UTC si viene naive
        return dt.replace(tzinfo=utcnow().tzinfo)
    return dt


def _get_subscription_window_or_raise(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
) -> tuple[SuscripcionModulo, UsageWindow]:
    """
    Busca la suscripción del módulo y devuelve su periodo actual.
    """
    sub = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == module_key)
        .first()
    )
    if not sub:
        raise ValueError(
            f"No existe suscripción para módulo={module_key.value} en negocio_id={negocio_id}"
        )

    if not sub.current_period_start or not sub.current_period_end:
        raise ValueError(f"Suscripción sin periodo válido: id={sub.id}")

    return sub, UsageWindow(
        period_start=_ensure_tz(sub.current_period_start),
        period_end=_ensure_tz(sub.current_period_end),
    )


def _get_or_create_counter(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
    window: UsageWindow,
) -> UsageCounter:
    """
    Obtiene el contador de uso del periodo; si no existe, lo crea.

    ✅ Enterprise: usamos SAVEPOINT (begin_nested) para que una colisión de unique
    NO haga rollback de toda la transacción del caller.
    """
    metric = (metric_key or "").strip()
    if not metric:
        raise ValueError("metric_key vacío no es válido")

    def _query():
        return (
            db.query(UsageCounter)
            .filter(UsageCounter.negocio_id == negocio_id)
            .filter(UsageCounter.module_key == module_key)
            .filter(UsageCounter.metric_key == metric)
            .filter(UsageCounter.period_start == window.period_start)
            .filter(UsageCounter.period_end == window.period_end)
        )

    row = _query().first()
    if row:
        return row

    # Crear nuevo con retry defensivo (concurrencia real)
    for _ in range(2):
        try:
            with db.begin_nested():  # SAVEPOINT
                row_new = UsageCounter(
                    negocio_id=negocio_id,
                    module_key=module_key,
                    metric_key=metric,
                    period_start=window.period_start,
                    period_end=window.period_end,
                    value=0.0,
                )
                db.add(row_new)
                db.flush()  # detecta UniqueConstraint sin commit
                return row_new
        except IntegrityError:
            # Otra transacción lo creó entre el first() y el insert.
            row2 = _query().first()
            if row2:
                return row2
            # si aún no aparece, repetimos 1 vez más
            continue

    # Último intento: si sigue sin aparecer, propagamos error (algo raro pasa)
    raise IntegrityError(
        statement=None,
        params=None,
        orig=Exception("No se pudo crear/obtener UsageCounter tras colisión."),
    )


# =========================================================
# API PUBLICA (SERVICIO)
# =========================================================

def get_usage_value(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
) -> float:
    """
    Retorna el uso actual (value) de una métrica en el periodo actual del módulo.
    Si no existe contador aún, devuelve 0.
    """
    _, window = _get_subscription_window_or_raise(db, negocio_id, module_key)

    metric = (metric_key or "").strip()
    if not metric:
        return 0.0

    row = (
        db.query(UsageCounter.value)
        .filter(UsageCounter.negocio_id == negocio_id)
        .filter(UsageCounter.module_key == module_key)
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
) -> float:
    """
    Incrementa (en el periodo actual) el contador de uso de una métrica.
    Retorna el nuevo valor.

    - delta debe ser positivo
    - No hace commit (lo gestiona el caller)
    """
    try:
        d = float(delta)
    except Exception:
        d = 0.0

    if d <= 0:
        return get_usage_value(db, negocio_id, module_key, metric_key)

    _, window = _get_subscription_window_or_raise(db, negocio_id, module_key)
    row = _get_or_create_counter(db, negocio_id, module_key, metric_key, window)

    row.value = float(row.value or 0.0) + d
    row.updated_at = utcnow()

    db.flush()
    return float(row.value)


def get_or_create_usage_counter(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
    metric_key: str,
) -> UsageCounter:
    """
    Retorna el UsageCounter del periodo actual (creándolo si no existe).
    Útil para el resolver de entitlements (para leer más de una métrica).
    """
    _, window = _get_subscription_window_or_raise(db, negocio_id, module_key)
    return _get_or_create_counter(db, negocio_id, module_key, metric_key, window)


def list_usage_for_module_current_period(
    db: Session,
    negocio_id: int,
    module_key: ModuleKey,
) -> dict[str, float]:
    """
    Devuelve todos los usage counters existentes para un módulo en su periodo actual.
    """
    _, window = _get_subscription_window_or_raise(db, negocio_id, module_key)

    rows = (
        db.query(UsageCounter.metric_key, UsageCounter.value)
        .filter(UsageCounter.negocio_id == negocio_id)
        .filter(UsageCounter.module_key == module_key)
        .filter(UsageCounter.period_start == window.period_start)
        .filter(UsageCounter.period_end == window.period_end)
        .all()
    )

    out: dict[str, float] = {}
    for k, v in rows:
        out[str(k)] = float(v or 0.0)
    return out
