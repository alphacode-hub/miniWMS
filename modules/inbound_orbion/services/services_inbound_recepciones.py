# modules/inbound_orbion/services/services_inbound_recepciones.py
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from core.models.inbound.recepciones import InboundRecepcion
from core.models.enums import RecepcionEstado
from core.models.inbound.proveedores import Proveedor  # ✅ correcto
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from core.models.time import utcnow



# ============================
#   HELPERS
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


def _parse_date_to_dt(value: str | None) -> datetime | None:
    v = _strip_or_none(value)
    if not v:
        return None
    try:
        d = date.fromisoformat(v)
        # midnight UTC (tz-aware si utcnow() lo es)
        base = utcnow()
        return base.replace(year=d.year, month=d.month, day=d.day, hour=0, minute=0, second=0, microsecond=0)
    except Exception as e:
        raise InboundDomainError(f"Fecha inválida: {value}") from e


def _next_codigo_recepcion(db: Session, negocio_id: int) -> str:
    year = datetime.utcnow().year
    prefix = f"INB-{year}-"

    max_code = db.execute(
        select(func.max(InboundRecepcion.codigo_recepcion))
        .where(InboundRecepcion.negocio_id == negocio_id)
        .where(InboundRecepcion.codigo_recepcion.like(f"{prefix}%"))
    ).scalar_one_or_none()

    if not max_code:
        return f"{prefix}000001"

    try:
        last = int(max_code.split("-")[-1])
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
        p = db.get(Proveedor, proveedor_id)
        if not p:
            raise InboundDomainError("Proveedor seleccionado no existe.")
        if hasattr(p, "negocio_id") and int(getattr(p, "negocio_id")) != int(negocio_id):
            raise InboundDomainError("Proveedor no pertenece a tu negocio.")
        return int(p.id)

    nombre = _strip_or_none(proveedor_nombre)
    if not nombre:
        return None

    stmt = select(Proveedor).where(func.lower(Proveedor.nombre) == nombre.lower())
    if hasattr(Proveedor, "negocio_id"):
        stmt = stmt.where(Proveedor.negocio_id == negocio_id)

    p = db.execute(stmt).scalar_one_or_none()
    if p:
        return int(p.id)

    p = Proveedor(nombre=nombre)
    if hasattr(Proveedor, "negocio_id"):
        setattr(p, "negocio_id", negocio_id)

    db.add(p)
    db.flush()
    return int(p.id)


def _validar_minimo_operativo(*, contenedor: str | None, patente_camion: str | None) -> None:
    """
    Regla soft-operativa: exige al menos contenedor o patente.
    (Evita recepciones 'vacías' y ayuda al control operacional)
    """
    if not _strip_or_none(contenedor) and not _strip_or_none(patente_camion):
        raise InboundDomainError("Debes ingresar al menos contenedor o patente del camión.")


def _validar_fechas(*, eta: datetime | None, real: datetime | None) -> None:
    """
    Evita inconsistencias: la fecha real no puede ser anterior a la ETA.
    """
    if eta and real and real < eta:
        raise InboundDomainError("La fecha real de recepción no puede ser anterior a la ETA.")


# ============================
#   CRUD
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
) -> list[InboundRecepcion]:
    stmt = select(InboundRecepcion).where(InboundRecepcion.negocio_id == negocio_id)

    if estado:
        try:
            stmt = stmt.where(InboundRecepcion.estado == RecepcionEstado[estado])
        except Exception:
            pass

    if desde:
        stmt = stmt.where(InboundRecepcion.created_at >= datetime(desde.year, desde.month, desde.day))
    if hasta:
        stmt = stmt.where(InboundRecepcion.created_at < datetime(hasta.year, hasta.month, hasta.day, 23, 59, 59))

    if q:
        qq = f"%{q.strip()}%"
        stmt = stmt.where(
            (InboundRecepcion.codigo_recepcion.ilike(qq)) |
            (InboundRecepcion.documento_ref.ilike(qq)) |
            (InboundRecepcion.contenedor.ilike(qq)) |
            (InboundRecepcion.patente_camion.ilike(qq))
        )

    stmt = stmt.order_by(InboundRecepcion.created_at.desc()).limit(limit)
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

    # ✅ Regla mínima operativa
    _validar_minimo_operativo(
        contenedor=data.get("contenedor"),
        patente_camion=data.get("patente_camion"),
    )

    # proveedor
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

    # ✅ Parseo fechas + validación
    eta = _parse_date_to_dt(data.get("fecha_estimada_llegada"))
    real = _parse_date_to_dt(data.get("fecha_recepcion"))
    _validar_fechas(eta=eta, real=real)

    r = InboundRecepcion(
        negocio_id=negocio_id,
        proveedor_id=prov_id,
        codigo_recepcion=codigo,
        documento_ref=_strip_or_none(data.get("documento_ref")),
        contenedor=_upper_or_none(data.get("contenedor")),
        patente_camion=_upper_or_none(data.get("patente_camion")),
        tipo_carga=_lower_or_none(data.get("tipo_carga")),  # ✅ normalizado
        fecha_estimada_llegada=eta,
        fecha_recepcion=real,
        observaciones=_strip_or_none(data.get("observaciones")),
        estado=estado_enum,
    )

    db.add(r)
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

    # ✅ Regla mínima operativa (considera los nuevos valores)
    _validar_minimo_operativo(
        contenedor=_strip_or_none(data.get("contenedor")) or r.contenedor,
        patente_camion=_strip_or_none(data.get("patente_camion")) or r.patente_camion,
    )


    r.documento_ref = _strip_or_none(data.get("documento_ref"))
    r.contenedor = _upper_or_none(data.get("contenedor"))
    r.patente_camion = _upper_or_none(data.get("patente_camion"))
    r.tipo_carga = _lower_or_none(data.get("tipo_carga"))  # ✅ normalizado

    # ✅ Parseo fechas + validación
    eta = _parse_date_to_dt(data.get("fecha_estimada_llegada"))
    real = _parse_date_to_dt(data.get("fecha_recepcion"))
    _validar_fechas(eta=eta, real=real)

    r.fecha_estimada_llegada = eta
    r.fecha_recepcion = real
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
