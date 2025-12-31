from __future__ import annotations

from datetime import datetime,timezone
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

    # si viene naive, asumimos UTC
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)

    if _CL_TZ:
        return dt.astimezone(_CL_TZ)

    return dt



def assume_cl_local_to_utc(dt: datetime | None) -> datetime | None:
    """
    Si dt viene naive, lo interpretamos como hora local Chile y lo convertimos a UTC tz-aware.
    Si viene aware, lo convertimos a UTC.
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        if _CL_TZ:
            dt_local = dt.replace(tzinfo=_CL_TZ)
            return dt_local.astimezone(timezone.utc)
        # fallback sin zoneinfo: asumimos UTC
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def ensure_utc_aware(dt: datetime | None) -> datetime | None:
    """
    Asegura tz-aware en UTC.
    - naive => UTC
    - aware => UTC
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)



def cl_datetime(
    dt: datetime | None,
    *,
    short: bool = False,
    no_tz: bool = False,
) -> str:
    """
    Formato Chile:
    - default:  DD-MM-YYYY HH:MM:SS CLST
    - short:    DD-MM-YYYY HH:MM
    - no_tz:    oculta zona horaria
    """
    if dt is None:
        return "-"

    dcl = to_cl_tz(dt) or dt

    if short:
        fmt = "%d-%m-%Y %H:%M"
    else:
        fmt = "%d-%m-%Y %H:%M:%S"

    base = dcl.strftime(fmt)

    if no_tz:
        return base

    tz = dcl.tzname() or ""
    return f"{base} {tz}".strip()


def cl_date(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    dcl = to_cl_tz(dt) or dt
    return dcl.strftime("%d-%m-%Y")


def cl_num(value: Any, decimals: int = 0) -> str:
    if value is None or value == "":
        return "-"
    try:
        num = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)

    q = Decimal("1") if decimals == 0 else Decimal("1." + ("0" * decimals))
    num = num.quantize(q)

    s = f"{num:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def clp(value: Any, decimals: int = 0) -> str:
    return f"$ {cl_num(value, decimals=decimals)}"
