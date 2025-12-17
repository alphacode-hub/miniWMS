# modules/inbound_orbion/services/services_inbound_pallets.py
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import Producto
from core.models.inbound.lineas import InboundLinea
from core.models.inbound.pallets import InboundPallet, InboundPalletItem
from core.models.enums import PalletEstado
from core.models.time import utcnow

from modules.inbound_orbion.services.inbound_linea_contract import (
    normalizar_linea,
    InboundLineaModo,
    InboundLineaContractError,
)

from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_config_inbound,
    validar_recepcion_editable,
)

# ============================
# Constantes enterprise
# ============================

_EPS = 1e-9
_TOL_REL = 0.02  # 2% tolerancia para consistencia si usuario ingresa ambos

# ============================
# Helpers parse/validación
# ============================

def _clean_str(v: Any) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s or None


def _to_float_or_none(v: Any) -> float | None:
    """Float > 0, si 0 o vacío => None"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().replace(",", ".")
        if s == "":
            return None
        v = s
    try:
        n = float(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor numérico inválido. Usa números (ej: 10 o 10.5).") from exc
    return n if n > 0 else None


def _to_float_allow_zero_or_none(v: Any) -> float | None:
    """Float >= 0, si vacío => None (para temperatura u otros campos que acepten 0)"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().replace(",", ".")
        if s == "":
            return None
        v = s
    try:
        n = float(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor numérico inválido.") from exc
    if n < 0:
        raise InboundDomainError("Este valor no puede ser negativo.")
    return n


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        if not s.isdigit():
            raise InboundDomainError("Valor entero inválido.")
        return int(s)
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor entero inválido.") from exc


def _calcular_peso_neto(peso_bruto_kg: float | None, peso_tara_kg: float | None) -> float | None:
    if peso_bruto_kg is None or peso_tara_kg is None:
        return None
    # ✅ enterprise: tara no puede exceder bruto
    if peso_tara_kg > peso_bruto_kg + _EPS:
        raise InboundDomainError("La tara no puede ser mayor que el peso bruto.")
    return round(float(peso_bruto_kg) - float(peso_tara_kg), 3)


def _assert_pallet_editable(pallet: InboundPallet) -> None:
    if pallet.estado in (PalletEstado.LISTO, PalletEstado.BLOQUEADO):
        raise InboundDomainError("Este pallet no se puede modificar porque ya está LISTO o BLOQUEADO.")


def _sum_asignado_por_linea_en_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    linea_id: int,
) -> tuple[float, float]:
    row = (
        db.query(
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPalletItem.linea_id == linea_id,
        )
        .first()
    )
    return float(row[0] or 0), float(row[1] or 0)


# ============================
# Conversión enterprise (línea/producto/override)
# ============================

def _resolver_peso_unitario_kg(linea: InboundLinea) -> float | None:
    """
    Fuente preferente:
    1) override en línea: peso_unitario_kg_override
    2) producto: peso_unitario_kg
    """
    v = getattr(linea, "peso_unitario_kg_override", None)
    if v is not None:
        try:
            n = float(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            pass

    prod: Producto | None = getattr(linea, "producto", None)
    if prod is not None:
        v2 = getattr(prod, "peso_unitario_kg", None)
        if v2 is not None:
            try:
                n2 = float(v2)
                return n2 if n2 > 0 else None
            except (TypeError, ValueError):
                pass

    return None


def _calc_kg_desde_cantidad(cantidad: float, peso_unitario_kg: float) -> float:
    return round(float(cantidad) * float(peso_unitario_kg), 3)


def _calc_cantidad_desde_kg(peso_kg: float, peso_unitario_kg: float) -> float:
    return round(float(peso_kg) / float(peso_unitario_kg), 3)


def _check_consistencia_si_ambos(
    *,
    cantidad: float | None,
    peso_kg: float | None,
    peso_unitario_kg: float | None,
) -> None:
    """
    Si el usuario ingresa ambos, validamos que no sean absurdamente inconsistentes
    cuando tenemos conversión.
    """
    if cantidad is None or peso_kg is None or peso_unitario_kg is None:
        return
    if cantidad <= 0 or peso_kg <= 0:
        return

    esperado = _calc_kg_desde_cantidad(cantidad, peso_unitario_kg)
    if esperado <= 0:
        return

    diff = abs(esperado - peso_kg)
    if diff <= 0.5:  # tolerancia absoluta pequeña en kg
        return
    if diff / max(esperado, _EPS) > _TOL_REL:
        raise InboundDomainError(
            f"Inconsistencia: con {cantidad:g} unidades y {peso_unitario_kg:g} kg/u, "
            f"esperamos ~{esperado:g} kg, pero ingresaste {peso_kg:g} kg."
        )


# ============================
# Crear pallet
# ============================

def crear_pallet_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    codigo_pallet: str,
    *,
    peso_bruto_kg: float | None = None,
    peso_tara_kg: float | None = None,
    bultos: int | None = None,
    temperatura_promedio: float | None = None,
    observaciones: str | None = None,
    creado_por_id: int | None = None,
) -> InboundPallet:
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    codigo_norm = (codigo_pallet or "").strip().upper()
    if not codigo_norm:
        raise InboundDomainError("El código del pallet es obligatorio.")

    dup = (
        db.query(InboundPallet.id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion.id,
            InboundPallet.codigo_pallet == codigo_norm,
        )
        .first()
    )
    if dup:
        raise InboundDomainError(f"El pallet con código '{codigo_norm}' ya existe en esta recepción.")

    # ✅ peso neto enterprise (valida tara <= bruto)
    neto = _calcular_peso_neto(peso_bruto_kg, peso_tara_kg)

    pallet = InboundPallet(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        codigo_pallet=codigo_norm,
        estado=PalletEstado.ABIERTO,
        peso_bruto_kg=peso_bruto_kg,
        peso_tara_kg=peso_tara_kg,
        peso_neto_kg=neto,
        bultos=bultos,
        temperatura_promedio=temperatura_promedio,
        observaciones=_clean_str(observaciones),
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    db.add(pallet)
    db.commit()
    db.refresh(pallet)
    return pallet


# ============================
# Agregar item (modo oficial + conversión persistida)
# ============================

def agregar_items_a_pallet(
    db: Session,
    negocio_id: int,
    pallet_id: int,
    items: Iterable[dict[str, Any]],
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, pallet.recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    try:
        for item_data in items:
            linea_id_raw = item_data.get("linea_id")
            if linea_id_raw is None:
                raise InboundDomainError("Cada ítem debe incluir 'linea_id'.")

            try:
                linea_id = int(linea_id_raw)
            except (TypeError, ValueError) as exc:
                raise InboundDomainError("Debes seleccionar una línea válida.") from exc

            linea = (
                db.query(InboundLinea)
                .filter(InboundLinea.id == linea_id, InboundLinea.negocio_id == negocio_id)
                .first()
            )
            if not linea:
                raise InboundDomainError("Línea inbound no encontrada para este negocio.")
            if linea.recepcion_id != recepcion.id:
                raise InboundDomainError("La línea seleccionada no pertenece a esta recepción.")

            # No duplicar línea dentro del mismo pallet
            if (
                db.query(InboundPalletItem.id)
                .filter(InboundPalletItem.pallet_id == pallet.id, InboundPalletItem.linea_id == linea.id)
                .first()
            ):
                raise InboundDomainError("Esta línea ya está asignada a este pallet.")

            # ✅ contrato: determina modo y base oficial
            try:
                view = normalizar_linea(linea, allow_draft=False)
            except InboundLineaContractError as exc:
                raise InboundDomainError(f"Línea inválida según contrato: {str(exc)}") from exc

            modo = view.modo

            cantidad_in = item_data.get("cantidad")
            peso_in = item_data.get("peso_kg")

            cantidad = None if cantidad_in is None else float(cantidad_in)
            peso_kg = None if peso_in is None else float(peso_in)

            # Sanitiza <=0 a None (enterprise)
            if cantidad is not None and cantidad <= 0:
                cantidad = None
            if peso_kg is not None and peso_kg <= 0:
                peso_kg = None

            if cantidad is None and peso_kg is None:
                raise InboundDomainError("Debes ingresar una cantidad o un peso mayor a cero.")

            # ✅ conversión disponible?
            peso_unitario = _resolver_peso_unitario_kg(linea)

            # Si usuario ingresó ambos, validamos consistencia cuando podemos
            _check_consistencia_si_ambos(cantidad=cantidad, peso_kg=peso_kg, peso_unitario_kg=peso_unitario)

            # ✅ regla por modo oficial (enterprise)
            if modo == InboundLineaModo.CANTIDAD:
                if cantidad is None:
                    raise InboundDomainError("Esta línea es por CANTIDAD: debes ingresar cantidad.")
                # Autocompleta kg si falta y hay conversión
                if peso_kg is None and peso_unitario is not None:
                    peso_kg = _calc_kg_desde_cantidad(cantidad, peso_unitario)

            else:  # PESO
                if peso_kg is None:
                    raise InboundDomainError("Esta línea es por PESO: debes ingresar kg.")
                # Autocompleta cantidad si falta y hay conversión
                if cantidad is None and peso_unitario is not None:
                    cantidad = _calc_cantidad_desde_kg(peso_kg, peso_unitario)

            # ✅ validación de pendientes (según modo oficial)
            cant_asig, kg_asig = _sum_asignado_por_linea_en_recepcion(
                db,
                negocio_id=negocio_id,
                recepcion_id=recepcion.id,
                linea_id=linea.id,
            )

            if modo == InboundLineaModo.CANTIDAD:
                base = view.base_cantidad
                if base is None or base <= 0:
                    raise InboundDomainError("La línea no tiene cantidad base válida para asignar.")
                pend = float(base) - float(cant_asig)
                if cantidad is None:
                    raise InboundDomainError("Debes ingresar cantidad.")
                if cantidad > pend + _EPS:
                    raise InboundDomainError(f"Cantidad supera el pendiente. Pendiente: {max(pend, 0):.3f}")

            else:  # PESO
                base = view.base_peso_kg
                if base is None or base <= 0:
                    raise InboundDomainError("La línea no tiene peso base válido para asignar.")
                pend = float(base) - float(kg_asig)
                if peso_kg is None:
                    raise InboundDomainError("Debes ingresar kg.")
                if peso_kg > pend + _EPS:
                    raise InboundDomainError(f"Peso supera el pendiente. Pendiente: {max(pend, 0):.3f} kg")

            # ✅ persistimos AMBOS si están disponibles (esto arregla tu “kg reales en 0”)
            item = InboundPalletItem(
                negocio_id=negocio_id,
                pallet_id=pallet.id,
                linea_id=linea.id,
                cantidad=(float(cantidad) if cantidad is not None else None),
                peso_kg=(float(peso_kg) if peso_kg is not None else None),
                created_at=utcnow(),
            )
            db.add(item)

            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise InboundDomainError("Esta línea ya está asignada a este pallet.") from exc

        pallet.updated_at = utcnow()
        db.commit()

    except InboundDomainError:
        db.rollback()
        raise

def _get_linea_cantidad_base(linea: InboundLinea) -> float | None:
    # ✅ Base oficial por cantidad viene del documento
    v = getattr(linea, "cantidad_documento", None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get_linea_kg_base(linea: InboundLinea) -> float | None:
    # ✅ Base oficial por peso viene del documento
    v = getattr(linea, "peso_kg", None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ============================
# Quitar item
# ============================

def quitar_item_de_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    pallet_item_id: int,
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id or pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("Pallet inbound no encontrado para esta recepción.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    item = db.get(InboundPalletItem, pallet_item_id)
    if not item or item.pallet_id != pallet.id:
        raise InboundDomainError("Ítem no encontrado para este pallet.")

    db.delete(item)
    pallet.updated_at = utcnow()
    db.commit()


# ============================
# Eliminar pallet
# ============================

def eliminar_pallet_inbound(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id or pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("Pallet inbound no encontrado para esta recepción.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    db.query(InboundPalletItem).filter(InboundPalletItem.pallet_id == pallet.id).delete()
    db.delete(pallet)
    db.commit()


# ============================
# Cerrar / Reabrir
# ============================

def marcar_pallet_listo(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int, user_id: int) -> None:
    pallet = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.id == pallet_id,
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .first()
    )
    if not pallet:
        raise InboundDomainError("Pallet no encontrado.")

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    if not (pallet.observaciones or "").strip():
        raise InboundDomainError("Para cerrar el pallet debes ingresar observaciones (mínimo algo descriptivo).")

    tiene_items = db.query(InboundPalletItem.id).filter(InboundPalletItem.pallet_id == pallet.id).first()
    if not tiene_items:
        raise InboundDomainError("No puedes cerrar un pallet sin líneas asignadas.")

    pallet.estado = PalletEstado.LISTO
    pallet.cerrado_por_id = user_id
    pallet.cerrado_at = utcnow()
    pallet.updated_at = utcnow()
    db.commit()


def reabrir_pallet(db: Session, negocio_id: int, recepcion_id: int, pallet_id: int) -> None:
    pallet = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.id == pallet_id,
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .first()
    )
    if not pallet:
        raise InboundDomainError("Pallet no encontrado.")

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    pallet.estado = PalletEstado.ABIERTO
    pallet.cerrado_por_id = None
    pallet.cerrado_at = None
    pallet.updated_at = utcnow()
    db.commit()
