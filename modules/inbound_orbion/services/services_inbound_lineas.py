# modules/inbound_orbion/services/services_inbound_lineas.py
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.inbound.lineas import InboundLinea
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
    validar_producto_para_negocio,
)

from modules.inbound_orbion.services.inbound_linea_contract import (
    InboundLineaModo,
    normalizar_linea,
    InboundLineaContractError,
)

MAX_LEN_LOTE: Final[int] = 120
MAX_LEN_UNIDAD: Final[int] = 40
MAX_LEN_OBS: Final[int] = 1500
MAX_LEN_BULTO_NAME: Final[int] = 60


# =========================================================
# Helpers robustos
# =========================================================

def _clean_str(v: Any, *, max_len: int | None = None) -> str | None:
    s = ("" if v is None else str(v)).strip()
    if not s:
        return None
    if max_len is not None and len(s) > max_len:
        return s[:max_len].rstrip()
    return s


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        if v == "":
            return None
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor numérico inválido.") from exc


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor entero inválido.") from exc


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        s = v.strip()
        try:
            return datetime.fromisoformat(s).date()
        except ValueError as exc:
            raise InboundDomainError("Fecha de vencimiento inválida (usa YYYY-MM-DD).") from exc
    raise InboundDomainError("Fecha de vencimiento inválida.")


def _ensure_linea_belongs(negocio_id: int, linea: InboundLinea) -> None:
    if int(getattr(linea, "negocio_id", 0)) != int(negocio_id):
        raise InboundDomainError("Línea inbound no pertenece a este negocio.")


def _get_config_flags(db: Session, negocio_id: int) -> dict[str, bool]:
    """
    Lee flags desde InboundConfig si existe.
    Defaults seguros si no existe.
    """
    try:
        from core.models import InboundConfig as InboundConfigModel  # type: ignore
    except Exception:
        return {"require_lote": False, "require_fecha_vencimiento": False, "require_temperatura": False}

    cfg = (
        db.query(InboundConfigModel)
        .filter(InboundConfigModel.negocio_id == negocio_id)
        .first()
    )

    if not cfg:
        return {"require_lote": False, "require_fecha_vencimiento": False, "require_temperatura": False}

    return {
        "require_lote": bool(getattr(cfg, "require_lote", False)),
        "require_fecha_vencimiento": bool(getattr(cfg, "require_fecha_vencimiento", False)),
        "require_temperatura": bool(getattr(cfg, "require_temperatura", False)),
    }


def _enforce_required_fields(
    *,
    require_lote: bool,
    require_fecha_vencimiento: bool,
    require_temperatura: bool,
    lote: str | None,
    fecha_vencimiento: date | None,
    temperatura_recibida: float | None,
) -> None:
    if require_lote and not lote:
        raise InboundDomainError("El lote es obligatorio para este negocio.")
    if require_fecha_vencimiento and fecha_vencimiento is None:
        raise InboundDomainError("La fecha de vencimiento es obligatoria para este negocio.")
    if require_temperatura and temperatura_recibida is None:
        raise InboundDomainError("La temperatura recibida es obligatoria para este negocio.")


# =========================================================
# Queries
# =========================================================

def listar_lineas_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> list[InboundLinea]:
    """
    Lista líneas de una recepción (multi-tenant segura).
    """
    recepcion = obtener_recepcion_segura(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

    stmt = select(InboundLinea).where(InboundLinea.recepcion_id == recepcion.id)

    if hasattr(InboundLinea, "negocio_id"):
        stmt = stmt.where(InboundLinea.negocio_id == negocio_id)

    if hasattr(InboundLinea, "activo"):
        stmt = stmt.where(InboundLinea.activo == 1)

    # drafts primero
    if hasattr(InboundLinea, "es_draft"):
        stmt = stmt.order_by(InboundLinea.es_draft.desc(), InboundLinea.id.desc())
    else:
        stmt = stmt.order_by(InboundLinea.id.desc())

    return list(db.execute(stmt).scalars().all())


def obtener_linea(
    db: Session,
    *,
    negocio_id: int,
    linea_id: int,
) -> InboundLinea:
    linea = db.get(InboundLinea, linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")
    _ensure_linea_belongs(negocio_id, linea)
    return linea


# =========================================================
# CRUD
# =========================================================

def crear_linea_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    producto_id: int,
    *,
    lote: str | None = None,
    fecha_vencimiento: date | datetime | str | None = None,
    cantidad_esperada: float | str | None = None,
    cantidad_recibida: float | str | None = None,
    unidad: str | None = None,
    temperatura_objetivo: float | str | None = None,
    temperatura_recibida: float | str | None = None,
    observaciones: str | None = None,
    peso_kg: float | str | None = None,
    bultos: int | str | None = None,
    peso_unitario_kg_override: float | str | None = None,
    unidades_por_bulto_override: int | str | None = None,
    peso_por_bulto_kg_override: float | str | None = None,
    nombre_bulto_override: str | None = None,
) -> InboundLinea:
    recepcion = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    producto = validar_producto_para_negocio(db, int(producto_id), negocio_id)

    flags = _get_config_flags(db, negocio_id)

    lote_norm = _clean_str(lote, max_len=MAX_LEN_LOTE)
    unidad_norm = _clean_str(unidad, max_len=MAX_LEN_UNIDAD) or getattr(producto, "unidad", None) or "unidad"
    obs_norm = _clean_str(observaciones, max_len=MAX_LEN_OBS)
    fv = _to_date(fecha_vencimiento)

    cant_doc = _to_float(cantidad_esperada)
    cant_rec = _to_float(cantidad_recibida)
    kg_doc = _to_float(peso_kg)
    temp_obj = _to_float(temperatura_objetivo)
    temp_rec = _to_float(temperatura_recibida)
    bult = _to_int(bultos)

    if bult is not None and bult < 0:
        raise InboundDomainError("Bultos no puede ser negativo.")

    _enforce_required_fields(
        require_lote=flags["require_lote"],
        require_fecha_vencimiento=flags["require_fecha_vencimiento"],
        require_temperatura=flags["require_temperatura"],
        lote=lote_norm,
        fecha_vencimiento=fv,
        temperatura_recibida=temp_rec,
    )

    # objetivo doc
    tiene_cant = (cant_doc is not None and cant_doc > 0)
    tiene_kg = (kg_doc is not None and kg_doc > 0)

    if not (tiene_cant or tiene_kg):
        raise InboundDomainError("Debes definir objetivo de documento: Cantidad (>0) o Kg (>0).")

    modo = InboundLineaModo.CANTIDAD if tiene_cant else InboundLineaModo.PESO

    cant_rec_norm = None if cant_rec is None else float(cant_rec)

    if modo == InboundLineaModo.CANTIDAD:
        if cant_doc is None or cant_doc <= 0:
            raise InboundDomainError("Cantidad esperada debe ser > 0 para líneas por cantidad.")
        if cant_rec_norm is not None and cant_rec_norm < 0:
            raise InboundDomainError("Cantidad recibida no puede ser negativa.")
    else:
        if kg_doc is None or kg_doc <= 0:
            raise InboundDomainError("Peso (kg) debe ser > 0 para líneas por peso.")
        if cant_rec_norm is not None and cant_rec_norm < 0:
            raise InboundDomainError("Cantidad recibida no puede ser negativa.")
        cant_rec_norm = 0.0  # DB: no-null

    # overrides
    pu_ov = _to_float(peso_unitario_kg_override)
    ub_ov = _to_int(unidades_por_bulto_override)
    pb_ov = _to_float(peso_por_bulto_kg_override)
    nb_ov = _clean_str(nombre_bulto_override, max_len=MAX_LEN_BULTO_NAME)

    if pu_ov is not None and pu_ov <= 0:
        raise InboundDomainError("Override peso unitario (kg) debe ser > 0.")
    if ub_ov is not None and ub_ov <= 0:
        raise InboundDomainError("Override unidades por bulto debe ser > 0.")
    if pb_ov is not None and pb_ov <= 0:
        raise InboundDomainError("Override peso por bulto (kg) debe ser > 0.")

    linea = InboundLinea(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        producto_id=producto.id,
        lote=lote_norm,
        fecha_vencimiento=fv,
        cantidad_documento=(float(cant_doc) if tiene_cant else None),
        cantidad_recibida=(float(cant_rec_norm) if cant_rec_norm is not None else 0.0),
        unidad=unidad_norm,
        temperatura_objetivo=temp_obj,
        temperatura_recibida=temp_rec,
        observaciones=obs_norm,
        peso_kg=(float(kg_doc) if tiene_kg else None),
        bultos=bult,
        peso_unitario_kg_override=pu_ov,
        unidades_por_bulto_override=ub_ov,
        peso_por_bulto_kg_override=pb_ov,
        nombre_bulto_override=nb_ov,
        es_draft=0,
        activo=1,
    )

    if hasattr(linea, "peso_recibido_kg"):
        setattr(linea, "peso_recibido_kg", None)

    try:
        _ = normalizar_linea(linea, allow_draft=False)
    except InboundLineaContractError as exc:
        raise InboundDomainError(f"Línea inválida según contrato: {str(exc)}") from exc

    db.add(linea)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear la línea (conflicto/duplicado).") from exc

    db.refresh(linea)
    return linea


def actualizar_linea_inbound(
    db: Session,
    *,
    negocio_id: int,
    linea_id: int,
    allow_draft: bool = False,
    finalize: bool = False,
    **updates: Any,
) -> InboundLinea:
    """
    Update enterprise-safe:
    - Solo si la recepción está editable
    - allow_draft=True permite guardar parcialmente sin exigir contrato/requeridos.
    - finalize=True fuerza validación completa (contrato + requeridos) y marca es_draft=0.
    """
    linea = obtener_linea(db, negocio_id=negocio_id, linea_id=linea_id)

    recepcion_id = int(getattr(linea, "recepcion_id"))
    _ = obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

    flags = _get_config_flags(db, negocio_id)

    # producto_id (permitimos asignar producto en draft)
    if "producto_id" in updates:
        pid = updates.get("producto_id")
        if pid is not None and str(pid).strip() != "":
            producto = validar_producto_para_negocio(db, int(pid), negocio_id)
            linea.producto_id = producto.id
        else:
            # permitir limpiar producto solo si draft y se quiere
            if allow_draft:
                linea.producto_id = None

    # Compat: frontend → DB
    if "cantidad_esperada" in updates:
        updates["cantidad_documento"] = updates.pop("cantidad_esperada")

    for field, value in list(updates.items()):
        if field == "producto_id":
            continue
        if not hasattr(linea, field):
            continue

        if field == "lote":
            value = _clean_str(value, max_len=MAX_LEN_LOTE)
        elif field == "unidad":
            value = _clean_str(value, max_len=MAX_LEN_UNIDAD)
        elif field == "observaciones":
            value = _clean_str(value, max_len=MAX_LEN_OBS)
        elif field == "fecha_vencimiento":
            value = _to_date(value)
        elif field in (
            "cantidad_documento",
            "cantidad_recibida",
            "temperatura_objetivo",
            "temperatura_recibida",
            "peso_kg",
            "peso_unitario_kg_override",
            "peso_por_bulto_kg_override",
        ):
            value = _to_float(value)
            if field == "cantidad_recibida" and value is None:
                value = 0.0
        elif field in ("bultos", "unidades_por_bulto_override"):
            value = _to_int(value)
        elif field == "nombre_bulto_override":
            value = _clean_str(value, max_len=MAX_LEN_BULTO_NAME)

        setattr(linea, field, value)

    # Siempre validamos negativos básicos (incluso en draft)
    if getattr(linea, "bultos", None) is not None and getattr(linea, "bultos") < 0:
        raise InboundDomainError("Bultos no puede ser negativo.")

    pu_ov = getattr(linea, "peso_unitario_kg_override", None)
    ub_ov = getattr(linea, "unidades_por_bulto_override", None)
    pb_ov = getattr(linea, "peso_por_bulto_kg_override", None)

    if pu_ov is not None and pu_ov <= 0:
        raise InboundDomainError("Override peso unitario (kg) debe ser > 0.")
    if ub_ov is not None and ub_ov <= 0:
        raise InboundDomainError("Override unidades por bulto debe ser > 0.")
    if pb_ov is not None and pb_ov <= 0:
        raise InboundDomainError("Override peso por bulto (kg) debe ser > 0.")

    # Si es finalize, exigimos todo
    if finalize:
        allow_draft = False

    if not allow_draft:
        # requeridos por config (solo si no estamos guardando borrador)
        _enforce_required_fields(
            require_lote=flags["require_lote"],
            require_fecha_vencimiento=flags["require_fecha_vencimiento"],
            require_temperatura=flags["require_temperatura"],
            lote=_clean_str(getattr(linea, "lote", None), max_len=MAX_LEN_LOTE),
            fecha_vencimiento=getattr(linea, "fecha_vencimiento", None),
            temperatura_recibida=getattr(linea, "temperatura_recibida", None),
        )

        # contrato final
        try:
            _ = normalizar_linea(linea, allow_draft=False)
        except InboundLineaContractError as exc:
            raise InboundDomainError(f"Línea inválida según contrato: {str(exc)}") from exc

        # si finaliza, baja el flag draft
        if hasattr(linea, "es_draft"):
            linea.es_draft = 0

    else:
        # borrador: contrato tolerante (si algo está mal, preferimos no romper guardado)
        # pero sí podemos intentar normalizar para detectar negativos / incoherencias graves
        try:
            _ = normalizar_linea(linea, allow_draft=True)
        except Exception:
            # no bloqueamos guardado de borrador por contrato
            pass

        # mantener es_draft=1 si existe
        if hasattr(linea, "es_draft"):
            linea.es_draft = 1

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar la línea (conflicto/duplicado).") from exc

    db.refresh(linea)
    return linea


def eliminar_linea_inbound(
    db: Session,
    *,
    negocio_id: int,
    linea_id: int,
) -> None:
    """
    Delete enterprise-safe:
    - Solo si la recepción está editable
    - Bloquea si hay dependencias (IntegrityError)
    """
    linea = obtener_linea(db, negocio_id=negocio_id, linea_id=linea_id)

    recepcion_id = int(getattr(linea, "recepcion_id"))
    _ = obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

    try:
        db.delete(linea)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError(
            "No se puede eliminar la línea porque tiene dependencias asociadas (pallets/items/documentos)."
        ) from exc
    except Exception as exc:
        db.rollback()
        raise InboundDomainError("No se pudo eliminar la línea.") from exc
