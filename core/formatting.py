# core/formatting.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_CL_TZ = ZoneInfo("America/Santiago") if ZoneInfo else None


def to_cl_tz(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # si viene naive, asumimos UTC (baseline ya usa utc-aware, pero por seguridad)
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    if _CL_TZ:
        return dt.astimezone(_CL_TZ)
    return dt


def cl_datetime(dt: datetime | None, with_tz: bool = True) -> str:
    """
    Formato Chile: dd-mm-YYYY HH:MM[:SS] (CLT/CLST)
    """
    if dt is None:
        return "-"
    dcl = to_cl_tz(dt) or dt
    tz = dcl.tzname() if with_tz else ""
    # sin microsegundos
    s = dcl.strftime("%d-%m-%Y %H:%M:%S")
    return f"{s} {tz}".strip()


def cl_date(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    dcl = to_cl_tz(dt) or dt
    return dcl.strftime("%d-%m-%Y")


def cl_num(value: Any, decimals: int = 0) -> str:
    """
    Formato numérico Chile:
    miles con punto, decimales con coma.
    Ej: 1234567.89 -> 1.234.567,89
    """
    if value is None or value == "":
        return "-"
    try:
        num = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)

    q = Decimal("1") if decimals == 0 else Decimal("1." + ("0" * decimals))
    num = num.quantize(q)

    # formateo US primero: 1,234,567.89
    s = f"{num:,.{decimals}f}"
    # swap a Chile: 1.234.567,89
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def clp(value: Any, decimals: int = 0) -> str:
    """
    Moneda CLP estándar: $ 1.234.567 (sin decimales por defecto)
    """
    return f"$ {cl_num(value, decimals=decimals)}"
