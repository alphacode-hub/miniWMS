# modules/inbound_orbion/services/services_inbound_incidencias.py
from __future__ import annotations

from typing import Optional, List

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from core.models.time import utcnow
from core.models.inbound.incidencias import InboundIncidencia
from core.models.inbound.fotos import InboundFoto
from core.models.enums import IncidenciaEstado, ModuleKey

from core.services.services_usage import increment_usage_dual

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

# ✅ para validar línea cuando se use producto de la recepción
from core.models.inbound.lineas import InboundLinea


# ============================================================
# Helpers
# ============================================================

def _u(s: str | None) -> str | None:
    s2 = (s or "").strip()
    return s2 or None


def _upper(s: str | None, *, default: str) -> str:
    s2 = (s or "").strip().upper()
    return s2 or default


def _norm_float(v: float | int | str | None) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception as exc:
        raise InboundDomainError("Cantidad afectada inválida.") from exc


def _validar_linea_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    linea_id: int,
) -> InboundLinea:
    stmt = (
        select(InboundLinea)
        .where(InboundLinea.id == linea_id)
        .where(InboundLinea.negocio_id == negocio_id)
        .where(InboundLinea.recepcion_id == recepcion_id)
    )
    linea = db.execute(stmt).scalar_one_or_none()
    if not linea:
        raise InboundDomainError("La línea seleccionada no existe o no pertenece a esta recepción.")
    return linea


# ============================================================
# CREAR
# ============================================================

def crear_incidencia(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    tipo: str,
    criticidad: str,
    titulo: Optional[str],
    detalle: Optional[str],
    pallet_id: Optional[int] = None,
    creado_por: str | None = None,

    # ✅ enterprise
    linea_id: int | None = None,
    cantidad_afectada: float | int | str | None = None,
    unidad: str | None = None,
    lote: str | None = None,
) -> InboundIncidencia:
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    # ✅ contrato: si viene línea, debe pertenecer a la misma recepción/negocio
    if linea_id is not None:
        _ = _validar_linea_recepcion(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion.id,
            linea_id=int(linea_id),
        )

    inc = InboundIncidencia(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,

        # mantenemos por compat, pero tu UI ya no depende de esto
        pallet_id=pallet_id,

        # ✅ enterprise
        linea_id=int(linea_id) if linea_id is not None else None,
        cantidad_afectada=_norm_float(cantidad_afectada),
        unidad=_u(unidad),
        lote=_u(lote),

        tipo=_upper(tipo, default="GENERAL"),
        criticidad=_upper(criticidad, default="MEDIA"),
        estado=IncidenciaEstado.CREADA.value,
        titulo=_u(titulo),
        detalle=_u(detalle),
        creado_por=_u(creado_por),
        created_at=utcnow(),
        activo=1,
    )

    db.add(inc)

    # ---------------------------------------------------------
    # ✅ USAGE (Strategy C)
    # Evento: CREAR incidencia
    # - OPERATIONAL + BILLABLE por el mismo evento
    # - Queda en la misma tx del request (commit lo hace la ruta/bridge)
    # ---------------------------------------------------------
    increment_usage_dual(
        db,
        negocio_id=negocio_id,
        module_key=ModuleKey.INBOUND,
        metric_key="incidencias_mes",
        delta=1.0,
    )

    db.flush()
    return inc


# ============================================================
# LISTAR
# ============================================================

def listar_incidencias_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    include_deleted: bool = False,
) -> List[InboundIncidencia]:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    stmt = (
        select(InboundIncidencia)
        .where(InboundIncidencia.negocio_id == negocio_id)
        .where(InboundIncidencia.recepcion_id == recepcion_id)
    )
    if not include_deleted:
        stmt = stmt.where(InboundIncidencia.activo == 1)

    stmt = stmt.order_by(InboundIncidencia.created_at.desc())
    return list(db.execute(stmt).scalars().all())


# ============================================================
# OBTENER SEGURA
# ============================================================

def obtener_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
    include_deleted: bool = False,
) -> InboundIncidencia:
    stmt = (
        select(InboundIncidencia)
        .where(InboundIncidencia.id == incidencia_id)
        .where(InboundIncidencia.negocio_id == negocio_id)
    )
    if not include_deleted:
        stmt = stmt.where(InboundIncidencia.activo == 1)

    inc = db.execute(stmt).scalar_one_or_none()
    if not inc:
        raise InboundDomainError("Incidencia no encontrada.")
    return inc


# ============================================================
# CAMBIOS DE ESTADO
# ============================================================

def marcar_en_analisis(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
) -> InboundIncidencia:
    inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)

    if inc.estado in (IncidenciaEstado.CERRADA.value, IncidenciaEstado.CANCELADA.value):
        raise InboundDomainError("No puedes pasar a EN_ANALISIS una incidencia cerrada/cancelada.")

    inc.estado = IncidenciaEstado.EN_ANALISIS.value
    db.flush()
    return inc


def cerrar_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
    resolucion: str | None = None,
    resuelto_por: str | None = None,
) -> InboundIncidencia:
    inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)

    if inc.estado == IncidenciaEstado.CERRADA.value:
        raise InboundDomainError("La incidencia ya está cerrada.")
    if inc.estado == IncidenciaEstado.CANCELADA.value:
        raise InboundDomainError("No puedes cerrar una incidencia cancelada (reábrela primero).")

    inc.estado = IncidenciaEstado.CERRADA.value
    inc.resolucion = _u(resolucion) or inc.resolucion
    inc.resuelto_por = _u(resuelto_por) or inc.resuelto_por
    inc.resuelto_en = utcnow()
    db.flush()
    return inc


def reabrir_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
) -> InboundIncidencia:
    inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)

    if inc.estado not in (IncidenciaEstado.CERRADA.value, IncidenciaEstado.CANCELADA.value):
        raise InboundDomainError("Solo se pueden reabrir incidencias cerradas o canceladas.")

    inc.estado = IncidenciaEstado.CREADA.value
    db.flush()
    return inc


def cancelar_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
    motivo: str | None = None,
    cancelado_por: str | None = None,
) -> InboundIncidencia:
    inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)

    if inc.estado == IncidenciaEstado.CANCELADA.value:
        raise InboundDomainError("La incidencia ya está cancelada.")
    if inc.estado == IncidenciaEstado.CERRADA.value:
        raise InboundDomainError("No puedes cancelar una incidencia cerrada (reábrela primero).")

    inc.estado = IncidenciaEstado.CANCELADA.value
    inc.motivo_cancelacion = _u(motivo) or inc.motivo_cancelacion
    inc.cancelado_por = _u(cancelado_por) or inc.cancelado_por
    inc.cancelado_en = utcnow()
    db.flush()
    return inc


# ============================================================
# SOFT DELETE
# ============================================================

def eliminar_incidencia_soft(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
    eliminado_por: str | None = None,
) -> None:
    inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id, include_deleted=True)

    if inc.activo == 0:
        return

    inc.activo = 0
    inc.eliminado_por = _u(eliminado_por) or inc.eliminado_por
    inc.eliminado_en = utcnow()
    db.flush()


def restaurar_incidencia_soft(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
) -> None:
    inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id, include_deleted=True)

    if inc.activo == 1:
        return

    inc.activo = 1
    db.flush()


# ============================================================
# FOTOS (referencia) ligadas a incidencia
# ============================================================

def listar_fotos_incidencia(
    db: Session,
    *,
    negocio_id: int,
    incidencia_id: int,
    include_deleted: bool = False,
) -> List[InboundFoto]:
    stmt = (
        select(InboundFoto)
        .where(InboundFoto.negocio_id == negocio_id)
        .where(InboundFoto.incidencia_id == incidencia_id)
    )
    if not include_deleted:
        stmt = stmt.where(InboundFoto.activo == 1)
    stmt = stmt.order_by(InboundFoto.creado_en.desc())
    return list(db.execute(stmt).scalars().all())


def agregar_foto_incidencia_ref(
    db: Session,
    *,
    negocio_id: int,
    incidencia: InboundIncidencia,
    titulo: str | None,
    nota: str | None,
    archivo_url: str | None,
    archivo_path: str | None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    creado_por: str | None = None,
) -> InboundFoto:
    if not (archivo_url or archivo_path):
        raise InboundDomainError("Debes indicar archivo_url o archivo_path.")

    foto = InboundFoto(
        negocio_id=negocio_id,
        recepcion_id=incidencia.recepcion_id,
        incidencia_id=incidencia.id,

        # mantenemos por compat
        pallet_id=getattr(incidencia, "pallet_id", None),

        titulo=_u(titulo),
        nota=_u(nota),
        archivo_url=_u(archivo_url),
        archivo_path=_u(archivo_path),
        mime_type=_u(mime_type),
        size_bytes=size_bytes,
        creado_por=_u(creado_por),
        creado_en=utcnow(),
        activo=1,
    )
    db.add(foto)
    db.flush()
    return foto


def eliminar_foto_soft(
    db: Session,
    *,
    negocio_id: int,
    foto_id: int,
    eliminado_por: str | None = None,
) -> None:
    stmt = (
        select(InboundFoto)
        .where(InboundFoto.id == foto_id)
        .where(InboundFoto.negocio_id == negocio_id)
    )
    foto = db.execute(stmt).scalar_one_or_none()
    if not foto:
        raise InboundDomainError("Foto no encontrada.")

    if foto.activo == 0:
        return

    foto.activo = 0
    foto.eliminado_por = _u(eliminado_por) or foto.eliminado_por
    foto.eliminado_en = utcnow()
    db.flush()


# ============================================================
# MÉTRICAS (conteo general)
# ============================================================

def obtener_resumen_incidencias(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> dict:
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    stmt = (
        select(InboundIncidencia)
        .where(InboundIncidencia.negocio_id == negocio_id)
        .where(InboundIncidencia.recepcion_id == recepcion_id)
        .where(InboundIncidencia.activo == 1)
    )

    rows = list(db.execute(stmt).scalars().all())

    total = len(rows)
    cerradas = sum(1 for r in rows if r.estado == IncidenciaEstado.CERRADA.value)
    canceladas = sum(1 for r in rows if r.estado == IncidenciaEstado.CANCELADA.value)
    abiertas = total - cerradas - canceladas
    criticas = sum(1 for r in rows if (r.criticidad or "").upper() in ("ALTA", "CRITICA"))

    return {
        "total": total,
        "abiertas": abiertas,
        "cerradas": cerradas,
        "canceladas": canceladas,
        "criticas": criticas,
    }


# ============================================================
# ✅ ENTERPRISE: RESUMEN CUANTITATIVO (explica gaps)
# - Totales por recepción
# - Totales por línea
# ============================================================

def _resolver_peso_unitario_kg_linea(linea: InboundLinea) -> float | None:
    v = getattr(linea, "peso_unitario_kg_override", None)
    if v is not None:
        try:
            n = float(v)
            return n if n > 0 else None
        except Exception:
            pass

    prod = getattr(linea, "producto", None)
    if prod is not None:
        v2 = getattr(prod, "peso_unitario_kg", None)
        if v2 is not None:
            try:
                n2 = float(v2)
                return n2 if n2 > 0 else None
            except Exception:
                pass

    return None


def obtener_resumen_incidencias_cuantitativo(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    include_cerradas: bool = True,
    exclude_canceladas: bool = True,
) -> dict:
    """
    Resumen cuantitativo enterprise para explicar gaps en conciliación.
    - qty = suma cantidad_afectada
    - kg = qty * kg/u de la línea (override o producto)
    """
    obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    stmt = (
        select(InboundIncidencia)
        .options(joinedload(InboundIncidencia.linea).joinedload(InboundLinea.producto))
        .where(InboundIncidencia.negocio_id == negocio_id)
        .where(InboundIncidencia.recepcion_id == recepcion_id)
        .where(InboundIncidencia.activo == 1)
    )

    if exclude_canceladas:
        stmt = stmt.where(InboundIncidencia.estado != IncidenciaEstado.CANCELADA.value)

    if not include_cerradas:
        stmt = stmt.where(InboundIncidencia.estado != IncidenciaEstado.CERRADA.value)

    rows = list(db.execute(stmt).scalars().all())

    tot_count = 0
    tot_qty = 0.0
    tot_kg = 0.0
    por_linea: dict[int, dict] = {}

    for inc in rows:
        tot_count += 1
        qty = float(_norm_float(getattr(inc, "cantidad_afectada", None)) or 0.0)

        kg_est = 0.0
        linea = getattr(inc, "linea", None)
        if linea is not None and qty > 0:
            kg_u = _resolver_peso_unitario_kg_linea(linea)
            if kg_u is not None and kg_u > 0:
                kg_est = qty * float(kg_u)

        tot_qty += qty
        tot_kg += kg_est

        linea_id = getattr(inc, "linea_id", None)
        if linea_id is not None:
            lid = int(linea_id)
            if lid not in por_linea:
                por_linea[lid] = {"count": 0, "qty": 0.0, "kg": 0.0}
            por_linea[lid]["count"] += 1
            por_linea[lid]["qty"] += qty
            por_linea[lid]["kg"] += kg_est

    def _r(v: float) -> float:
        return round(float(v), 3)

    return {
        "totales": {"count": int(tot_count), "qty": _r(tot_qty), "kg": _r(tot_kg)},
        "por_linea": {
            int(k): {"count": int(v["count"]), "qty": _r(v["qty"]), "kg": _r(v["kg"])}
            for k, v in por_linea.items()
        },
        "meta": {"include_cerradas": include_cerradas, "exclude_canceladas": exclude_canceladas},
    }
