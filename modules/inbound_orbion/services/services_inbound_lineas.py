# modules/inbound_orbion/services/services_inbound_lineas.py

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models.inbound import InboundLinea
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
    obtener_recepcion_segura,
    validar_producto_para_negocio,
)


# ============================
#   TIME (UTC)
# ============================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================
#   VALIDACIONES / NORMALIZADORES
# ============================

MAX_LEN_LOTE: Final[int] = 120
MAX_LEN_UNIDAD: Final[int] = 40
MAX_LEN_OBS: Final[int] = 1500


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
    try:
        return float(v)
    except (TypeError, ValueError):
        raise InboundDomainError("Valor numérico inválido.")


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise InboundDomainError("Valor entero inválido.")


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        s = v.strip()
        # acepta YYYY-MM-DD
        try:
            return datetime.fromisoformat(s).date()
        except ValueError as exc:
            raise InboundDomainError("Fecha de vencimiento inválida (usa YYYY-MM-DD).") from exc
    raise InboundDomainError("Fecha de vencimiento inválida.")


def _require_positive(name: str, v: float | None, *, allow_zero: bool = False) -> None:
    if v is None:
        return
    if allow_zero:
        if v < 0:
            raise InboundDomainError(f"{name} no puede ser negativo.")
    else:
        if v <= 0:
            raise InboundDomainError(f"{name} debe ser mayor a 0.")


def _ensure_linea_belongs(db: Session, negocio_id: int, linea: InboundLinea) -> None:
    """
    Multi-tenant estricta:
    - Si linea tiene negocio_id, validamos directo.
    - Si no, validamos por pertenencia de la recepción.
    """
    if hasattr(InboundLinea, "negocio_id"):
        if getattr(linea, "negocio_id", None) != negocio_id:
            raise InboundDomainError("Línea inbound no pertenece a este negocio.")
        return
    _ = obtener_recepcion_segura(db, int(getattr(linea, "recepcion_id")), negocio_id)


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


def _get_config_flags(db: Session, negocio_id: int) -> dict[str, bool]:
    """
    Sin acoplarse a una 'InboundConfig' dataclass antigua:
    - Preferimos leer flags desde el modelo InboundConfig (si existe en core.models).
    - Si no existe o no hay registro, defaults seguros.
    """
    try:
        from core.models import InboundConfig as InboundConfigModel  # type: ignore
    except Exception:
        return {
            "require_lote": False,
            "require_fecha_vencimiento": False,
            "require_temperatura": False,
        }

    cfg = (
        db.query(InboundConfigModel)
        .filter(InboundConfigModel.negocio_id == negocio_id)
        .first()
    )
    # Defaults razonables
    if not cfg:
        return {
            "require_lote": False,
            "require_fecha_vencimiento": False,
            "require_temperatura": True,  # suele ser crítico en frío
        }

    return {
        "require_lote": bool(getattr(cfg, "require_lote", False)),
        "require_fecha_vencimiento": bool(getattr(cfg, "require_fecha_vencimiento", False)),
        "require_temperatura": bool(getattr(cfg, "require_temperatura", False)),
    }


# ============================
#   LÍNEAS DE RECEPCIÓN
# ============================

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
) -> InboundLinea:
    """
    Crea una línea inbound.

    Enterprise:
    - Recepción editable (workflow).
    - Producto pertenece al negocio y está activo.
    - Validaciones numéricas y requeridos según config.
    - Multi-tenant reforzado (negocio_id si existe).
    """
    recepcion = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    producto = validar_producto_para_negocio(db, int(producto_id), negocio_id)

    flags = _get_config_flags(db, negocio_id)

    lote_norm = _clean_str(lote, max_len=MAX_LEN_LOTE)
    unidad_norm = _clean_str(unidad, max_len=MAX_LEN_UNIDAD) or getattr(producto, "unidad", None)
    obs_norm = _clean_str(observaciones, max_len=MAX_LEN_OBS)

    fv = _to_date(fecha_vencimiento)
    cant_esp = _to_float(cantidad_esperada)
    cant_rec = _to_float(cantidad_recibida)
    temp_obj = _to_float(temperatura_objetivo)
    temp_rec = _to_float(temperatura_recibida)
    peso = _to_float(peso_kg)
    bult = _to_int(bultos)

    _require_positive("Cantidad esperada", cant_esp, allow_zero=False)
    _require_positive("Cantidad recibida", cant_rec, allow_zero=True)
    _require_positive("Peso (kg)", peso, allow_zero=True)
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

    linea_kwargs: dict[str, Any] = {
        "recepcion_id": recepcion.id,
        "producto_id": producto.id,
        "lote": lote_norm,
        "fecha_vencimiento": fv,

        # ✅ map enterprise -> tu BD
        "cantidad_documento": cant_esp,     # antes: cantidad_esperada
        "cantidad_recibida": cant_rec,
        "unidad": unidad_norm,

        "temperatura_objetivo": temp_obj,
        "temperatura_recibida": temp_rec,
        "observaciones": obs_norm,

        "peso_kg": peso,
        "bultos": bult,
    }


    if hasattr(InboundLinea, "negocio_id"):
        linea_kwargs["negocio_id"] = negocio_id
    if hasattr(InboundLinea, "creado_en"):
        linea_kwargs["creado_en"] = utcnow()

    linea = InboundLinea(**linea_kwargs)

    db.add(linea)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # típico: unique constraints (por ejemplo, recepcion_id + producto_id + lote)
        raise InboundDomainError("No se pudo crear la línea (conflicto/duplicado).") from exc

    db.refresh(linea)
    return linea


def actualizar_linea_inbound(
    db: Session,
    negocio_id: int,
    linea_id: int,
    **updates: Any,
) -> InboundLinea:
    """
    Actualiza una línea inbound.

    Enterprise:
    - Validación multi-tenant.
    - Recepción editable.
    - Validación de producto si cambia producto_id.
    - Normalización de campos + enforcement de requeridos según config.
    """
    linea = db.get(InboundLinea, linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")

    _ensure_linea_belongs(db, negocio_id, linea)

    recepcion_id = int(getattr(linea, "recepcion_id"))
    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)

    flags = _get_config_flags(db, negocio_id)

    # producto_id
    if "producto_id" in updates and updates["producto_id"] is not None:
        producto = validar_producto_para_negocio(db, int(updates["producto_id"]), negocio_id)
        setattr(linea, "producto_id", producto.id)

    # normalizaciones por campo
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
        elif field in ("cantidad_esperada", "cantidad_recibida", "temperatura_objetivo", "temperatura_recibida", "peso_kg"):
            value = _to_float(value)
        elif field == "bultos":
            value = _to_int(value)

        setattr(linea, field, value)

    # Validaciones numéricas post-update
    cant_esp = getattr(linea, "cantidad_esperada", None)
    cant_rec = getattr(linea, "cantidad_recibida", None)
    peso = getattr(linea, "peso_kg", None)
    bult = getattr(linea, "bultos", None)

    _require_positive("Cantidad esperada", cant_esp, allow_zero=False)
    _require_positive("Cantidad recibida", cant_rec, allow_zero=True)
    _require_positive("Peso (kg)", peso, allow_zero=True)
    if bult is not None and bult < 0:
        raise InboundDomainError("Bultos no puede ser negativo.")

    # Requeridos según config
    _enforce_required_fields(
        require_lote=flags["require_lote"],
        require_fecha_vencimiento=flags["require_fecha_vencimiento"],
        require_temperatura=flags["require_temperatura"],
        lote=_clean_str(getattr(linea, "lote", None), max_len=MAX_LEN_LOTE),
        fecha_vencimiento=getattr(linea, "fecha_vencimiento", None),
        temperatura_recibida=getattr(linea, "temperatura_recibida", None),
    )

    if hasattr(InboundLinea, "actualizado_en"):
        setattr(linea, "actualizado_en", utcnow())

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar la línea (conflicto/duplicado).") from exc

    db.refresh(linea)
    return linea


def eliminar_linea_inbound(
    db: Session,
    negocio_id: int,
    linea_id: int,
) -> None:
    """
    Elimina una línea inbound.
    Enterprise:
    - Multi-tenant
    - Recepción editable
    - Manejo de integridad (si hay pallets/items asociados, falla con mensaje claro)
    """
    linea = db.get(InboundLinea, linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")

    _ensure_linea_belongs(db, negocio_id, linea)

    recepcion_id = int(getattr(linea, "recepcion_id"))
    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)

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
