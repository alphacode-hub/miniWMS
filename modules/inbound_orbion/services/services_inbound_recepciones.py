# modules/inbound_orbion/services/services_inbound_recepciones.py
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_

from core.models.time import utcnow
from core.models.enums import RecepcionEstado, ModuleKey
from core.models.inbound.recepciones import InboundRecepcion
from core.models.inbound.proveedores import Proveedor

from core.services.services_usage import increment_usage_dual
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_CL_TZ = ZoneInfo("America/Santiago") if ZoneInfo else None


# ============================
# HELPERS
# ============================

def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


def _upper_or_none(value: str | None) -> str | None:
    v = _strip_or_none(value)
    return v.upper() if v else None


def _lower_or_none(value: str | None) -> str | None:
    v = _strip_or_none(value)
    return v.lower() if v else None


def _date_iso_to_utc_midnight_from_cl(value: str | None) -> datetime | None:
    """
    Entrada: 'YYYY-MM-DD' desde <input type="date">.
    Regla enterprise:
      - Interpretar como 00:00 en America/Santiago
      - Convertir a UTC tz-aware para persistir (baseline)
    """
    v = _strip_or_none(value)
    if not v:
        return None

    try:
        d = date.fromisoformat(v)
    except Exception as e:
        raise InboundDomainError(f"Fecha inválida: {value}") from e

    base_utc = utcnow()  # tz-aware UTC (baseline)
    dt_utc = base_utc.replace(year=d.year, month=d.month, day=d.day, hour=0, minute=0, second=0, microsecond=0)

    if not _CL_TZ:
        return dt_utc

    dt_cl = dt_utc.astimezone(_CL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return dt_cl.astimezone(base_utc.tzinfo)


def _day_bounds_utc(desde: date | None, hasta: date | None) -> tuple[datetime | None, datetime | None]:
    """
    Rango [desde 00:00, hasta 23:59:59.999999] en UTC tz-aware.
    El input 'desde/hasta' son date (sin hora).
    """
    if not desde and not hasta:
        return None, None

    base = utcnow()
    dt_from = None
    dt_to = None

    if desde:
        dt_from = base.replace(year=desde.year, month=desde.month, day=desde.day, hour=0, minute=0, second=0, microsecond=0)

    if hasta:
        dt_to = base.replace(year=hasta.year, month=hasta.month, day=hasta.day, hour=23, minute=59, second=59, microsecond=999999)

    return dt_from, dt_to


def _next_codigo_recepcion(db: Session, negocio_id: int) -> str:
    year = utcnow().year
    prefix = f"INB-{year}-"

    max_code = db.execute(
        select(func.max(InboundRecepcion.codigo_recepcion))
        .where(InboundRecepcion.negocio_id == negocio_id)
        .where(InboundRecepcion.codigo_recepcion.like(f"{prefix}%"))
    ).scalar_one_or_none()

    if not max_code:
        return f"{prefix}000001"

    try:
        last = int(str(max_code).split("-")[-1])
    except Exception:
        last = 0

    return f"{prefix}{last + 1:06d}"


def _resolver_proveedor_id(
    db: Session,
    *,
    negocio_id: int,
    proveedor_id: int | None,
    proveedor_nombre: str | None,
) -> int | None:
    if proveedor_id:
        p = db.get(Proveedor, int(proveedor_id))
        if not p:
            raise InboundDomainError("Proveedor seleccionado no existe.")
        if int(getattr(p, "negocio_id", 0) or 0) != int(negocio_id):
            raise InboundDomainError("Proveedor no pertenece a tu negocio.")
        return int(p.id)

    nombre = _strip_or_none(proveedor_nombre)
    if not nombre:
        return None

    stmt = (
        select(Proveedor)
        .where(Proveedor.negocio_id == negocio_id)
        .where(func.lower(Proveedor.nombre) == nombre.lower())
    )
    p = db.execute(stmt).scalar_one_or_none()
    if p:
        return int(p.id)

    p = Proveedor(nombre=nombre, negocio_id=negocio_id)
    db.add(p)
    db.flush()
    return int(p.id)


def _validar_minimo_operativo(*, contenedor: str | None, patente_camion: str | None) -> None:
    if not _strip_or_none(contenedor) and not _strip_or_none(patente_camion):
        raise InboundDomainError("Debes ingresar al menos contenedor o patente del camión.")


def _validar_fechas(*, eta: datetime | None, real: datetime | None) -> None:
    if eta and real and real < eta:
        raise InboundDomainError("La fecha real de recepción no puede ser anterior a la ETA.")


# ============================
# CRUD
# ============================

def listar_recepciones(
    db: Session,
    *,
    negocio_id: int,
    q: str | None = None,
    estado: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limit: int = 80,
    **kwargs: Any,
) -> list[InboundRecepcion]:
    if q is None:
        q = _strip_or_none(kwargs.get("query")) or _strip_or_none(kwargs.get("texto"))

    stmt = select(InboundRecepcion).where(InboundRecepcion.negocio_id == negocio_id)

    if estado:
        try:
            stmt = stmt.where(InboundRecepcion.estado == RecepcionEstado[estado])
        except Exception:
            pass

    dt_from, dt_to = _day_bounds_utc(desde, hasta)
    if dt_from:
        stmt = stmt.where(InboundRecepcion.created_at >= dt_from)
    if dt_to:
        stmt = stmt.where(InboundRecepcion.created_at <= dt_to)

    if q:
        qq = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                func.lower(InboundRecepcion.codigo_recepcion).like(func.lower(qq)),
                func.lower(InboundRecepcion.documento_ref).like(func.lower(qq)),
                func.lower(InboundRecepcion.contenedor).like(func.lower(qq)),
                func.lower(InboundRecepcion.patente_camion).like(func.lower(qq)),
            )
        )

    stmt = stmt.order_by(InboundRecepcion.created_at.desc()).limit(int(limit))
    return list(db.execute(stmt).scalars().all())


def obtener_recepcion(db: Session, negocio_id: int, recepcion_id: int) -> InboundRecepcion:
    r = db.get(InboundRecepcion, recepcion_id)
    if not r or int(r.negocio_id) != int(negocio_id):
        raise InboundDomainError("Recepción no encontrada.")
    return r


def crear_recepcion(
    db: Session,
    *,
    negocio_id: int,
    data: dict[str, Any],
) -> InboundRecepcion:
    codigo = _strip_or_none(data.get("codigo_recepcion")) or _next_codigo_recepcion(db, negocio_id)

    _validar_minimo_operativo(
        contenedor=data.get("contenedor"),
        patente_camion=data.get("patente_camion"),
    )

    prov_id = _resolver_proveedor_id(
        db,
        negocio_id=negocio_id,
        proveedor_id=int(data["proveedor_id"]) if data.get("proveedor_id") else None,
        proveedor_nombre=data.get("proveedor_nombre"),
    )

    estado_str = _strip_or_none(data.get("estado")) or "PRE_REGISTRADO"
    try:
        estado_enum = RecepcionEstado[estado_str]
    except Exception:
        estado_enum = RecepcionEstado.PRE_REGISTRADO

    eta = _date_iso_to_utc_midnight_from_cl(data.get("fecha_estimada_llegada"))
    real = _date_iso_to_utc_midnight_from_cl(data.get("fecha_recepcion"))
    _validar_fechas(eta=eta, real=real)

    r = InboundRecepcion(
        negocio_id=negocio_id,
        proveedor_id=prov_id,
        codigo_recepcion=codigo,
        documento_ref=_strip_or_none(data.get("documento_ref")),
        contenedor=_upper_or_none(data.get("contenedor")),
        patente_camion=_upper_or_none(data.get("patente_camion")),
        tipo_carga=_lower_or_none(data.get("tipo_carga")),
        fecha_estimada_llegada=eta,
        fecha_recepcion=real,
        observaciones=_strip_or_none(data.get("observaciones")),
        estado=estado_enum,
    )

    # ------------------------------
    # ✅ USAGE (Strategy C)
    # Evento: CREAR recepción
    # - OPERATIONAL + BILLABLE por el mismo evento
    # - Misma transacción (si falla commit, no queda usage sucio)
    # ------------------------------
    db.add(r)
    increment_usage_dual(
        db,
        negocio_id=negocio_id,
        module_key=ModuleKey.INBOUND,
        metric_key="recepciones_mes",
        delta=1.0,
    )

    db.commit()
    db.refresh(r)
    return r


def actualizar_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    data: dict[str, Any],
) -> InboundRecepcion:
    r = obtener_recepcion(db, negocio_id, recepcion_id)

    if _strip_or_none(data.get("codigo_recepcion")):
        r.codigo_recepcion = _strip_or_none(data.get("codigo_recepcion"))

    _validar_minimo_operativo(
        contenedor=_strip_or_none(data.get("contenedor")) or r.contenedor,
        patente_camion=_strip_or_none(data.get("patente_camion")) or r.patente_camion,
    )

    if data.get("documento_ref") is not None:
        r.documento_ref = _strip_or_none(data.get("documento_ref"))

    if data.get("contenedor") is not None:
        r.contenedor = _upper_or_none(data.get("contenedor"))
    if data.get("patente_camion") is not None:
        r.patente_camion = _upper_or_none(data.get("patente_camion"))
    if data.get("tipo_carga") is not None:
        r.tipo_carga = _lower_or_none(data.get("tipo_carga"))

    eta = (
        _date_iso_to_utc_midnight_from_cl(data.get("fecha_estimada_llegada"))
        if data.get("fecha_estimada_llegada") is not None
        else r.fecha_estimada_llegada
    )
    real = (
        _date_iso_to_utc_midnight_from_cl(data.get("fecha_recepcion"))
        if data.get("fecha_recepcion") is not None
        else r.fecha_recepcion
    )
    _validar_fechas(eta=eta, real=real)

    r.fecha_estimada_llegada = eta
    r.fecha_recepcion = real

    if data.get("observaciones") is not None:
        r.observaciones = _strip_or_none(data.get("observaciones"))

    prov_id = _resolver_proveedor_id(
        db,
        negocio_id=negocio_id,
        proveedor_id=int(data["proveedor_id"]) if data.get("proveedor_id") else None,
        proveedor_nombre=data.get("proveedor_nombre"),
    )
    r.proveedor_id = prov_id

    estado_str = _strip_or_none(data.get("estado"))
    if estado_str:
        try:
            r.estado = RecepcionEstado[estado_str]
        except Exception:
            pass

    db.commit()
    db.refresh(r)
    return r
