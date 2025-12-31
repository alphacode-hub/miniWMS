# modules/inbound_orbion/services/services_inbound_pallets.py
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import Producto
from core.models.enums import PalletEstado
from core.models.inbound.lineas import InboundLinea
from core.models.inbound.pallets import InboundPallet, InboundPalletItem
from core.models.time import utcnow

from modules.inbound_orbion.services.inbound_linea_contract import (
    InboundLineaContractError,
    InboundLineaModo,
    normalizar_linea,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_editable,
)

# Reconciliación (hook enterprise)
from modules.inbound_orbion.services.services_inbound_reconciliacion import reconciliar_recepcion


# ============================
# Constantes enterprise
# ============================

_EPS = 1e-9


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
    """Float >= 0, permite 0. Vacío => None"""
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
        n = int(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError("Valor entero inválido.") from exc
    if n < 0:
        raise InboundDomainError("Este valor no puede ser negativo.")
    return n


def _calcular_peso_neto(peso_bruto_kg: float | None, peso_tara_kg: float | None) -> float | None:
    if peso_bruto_kg is None or peso_tara_kg is None:
        return None
    if peso_tara_kg > peso_bruto_kg + _EPS:
        raise InboundDomainError("La tara no puede ser mayor que el peso bruto.")
    return round(float(peso_bruto_kg) - float(peso_tara_kg), 3)


# ============================
# Guards enterprise (centralizados)
# ============================

def _assert_pallet_no_bloqueado(pallet: InboundPallet) -> None:
    if pallet.estado == PalletEstado.BLOQUEADO:
        raise InboundDomainError("Este pallet está BLOQUEADO y no admite modificaciones.")


def _assert_pallet_editable(pallet: InboundPallet) -> None:
    if pallet.estado in (PalletEstado.LISTO, PalletEstado.BLOQUEADO):
        raise InboundDomainError("Este pallet no se puede modificar porque ya está LISTO o BLOQUEADO.")


def obtener_pallet_seguro(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> InboundPallet:
    pallet = (
        db.query(InboundPallet)
        .filter(
            InboundPallet.id == pallet_id,
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .first()
    )
    if pallet is None:
        raise InboundDomainError("Pallet inbound no encontrado para esta recepción.")
    return pallet


def obtener_pallet_editable(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> InboundPallet:
    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)
    _assert_pallet_editable(pallet)
    return pallet


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


# ============================
# Queries enterprise
# ============================

def _sum_asignado_por_linea_en_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    linea_id: int,
    exclude_pallet_id: int | None = None,
) -> tuple[float, float]:
    """
    Suma de asignación para validación de pendiente.
    Regla: usamos el eje REAL oficial:
    - Cantidad: suma(InboundPalletItem.cantidad)
    - Peso: suma(InboundPalletItem.peso_kg)
    """
    q = (
        db.query(
            func.coalesce(func.sum(InboundPalletItem.cantidad), 0.0),
            func.coalesce(func.sum(InboundPalletItem.peso_kg), 0.0),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPalletItem.linea_id == linea_id,
        )
    )
    if exclude_pallet_id is not None:
        q = q.filter(InboundPallet.id != exclude_pallet_id)

    row = q.first()
    return float(row[0] or 0.0), float(row[1] or 0.0)


def _obtener_linea_segura(db: Session, *, negocio_id: int, recepcion_id: int, linea_id: int) -> InboundLinea:
    linea = (
        db.query(InboundLinea)
        .filter(
            InboundLinea.id == linea_id,
            InboundLinea.negocio_id == negocio_id,
            InboundLinea.recepcion_id == recepcion_id,
            InboundLinea.activo == 1,
        )
        .first()
    )
    if linea is None:
        raise InboundDomainError("Línea inbound no encontrada para este negocio o recepción.")
    return linea


# ============================
# Crear / Editar pallet (metadata)
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
    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)

    codigo_norm = (codigo_pallet or "").strip().upper()
    if not codigo_norm:
        raise InboundDomainError("El código del pallet es obligatorio.")

    # Validaciones enterprise adicionales (antes de constraints DB)
    if bultos is not None and int(bultos) < 0:
        raise InboundDomainError("Bultos no puede ser negativo.")
    if temperatura_promedio is not None:
        _ = _to_float_allow_zero_or_none(temperatura_promedio)

    dup = (
        db.query(InboundPallet.id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPallet.codigo_pallet == codigo_norm,
        )
        .first()
    )
    if dup:
        raise InboundDomainError(f"El pallet con código '{codigo_norm}' ya existe en esta recepción.")

    neto = _calcular_peso_neto(peso_bruto_kg, peso_tara_kg)

    pallet = InboundPallet(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
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

    try:
        db.add(pallet)
        db.commit()
        db.refresh(pallet)
        return pallet
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear el pallet. Verifica que el código no esté duplicado.") from exc


def editar_pallet_inbound(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    bultos: int | None = None,
    temperatura_promedio: float | None = None,
    observaciones: str | None = None,
    peso_bruto_kg: float | None = None,
    peso_tara_kg: float | None = None,
) -> InboundPallet:
    pallet = obtener_pallet_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    # ✅ Enterprise: soporta edición parcial (si mandas solo tara o solo bruto, conserva el otro)
    new_bruto = peso_bruto_kg if peso_bruto_kg is not None else pallet.peso_bruto_kg
    new_tara = peso_tara_kg if peso_tara_kg is not None else pallet.peso_tara_kg
    neto = _calcular_peso_neto(new_bruto, new_tara)

    pallet.bultos = bultos
    pallet.temperatura_promedio = temperatura_promedio
    pallet.observaciones = _clean_str(observaciones)
    pallet.peso_bruto_kg = new_bruto
    pallet.peso_tara_kg = new_tara
    pallet.peso_neto_kg = neto
    pallet.updated_at = utcnow()

    db.commit()
    db.refresh(pallet)
    return pallet


# ============================
# Agregar items (fuente de verdad)
# ============================

def agregar_items_a_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    items: Iterable[dict[str, Any]],
) -> None:
    pallet = obtener_pallet_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    try:
        for item_data in items:
            linea_id_raw = item_data.get("linea_id")
            if linea_id_raw is None:
                raise InboundDomainError("Cada ítem debe incluir 'linea_id'.")

            try:
                linea_id = int(linea_id_raw)
            except (TypeError, ValueError) as exc:
                raise InboundDomainError("Debes seleccionar una línea válida.") from exc

            linea = _obtener_linea_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id, linea_id=linea_id)

            # No duplicar línea dentro del mismo pallet (defensa + unique constraint)
            if (
                db.query(InboundPalletItem.id)
                .filter(InboundPalletItem.pallet_id == pallet.id, InboundPalletItem.linea_id == linea.id)
                .first()
            ):
                raise InboundDomainError("Esta línea ya está asignada a este pallet.")

            # Contrato línea
            try:
                view = normalizar_linea(linea, allow_draft=False)
            except InboundLineaContractError as exc:
                raise InboundDomainError(f"Línea inválida según contrato: {str(exc)}") from exc

            modo = view.modo

            # Inputs UI
            cantidad_parsed = _to_float_or_none(item_data.get("cantidad"))
            peso_parsed = _to_float_or_none(item_data.get("peso_kg"))

            peso_unitario = _resolver_peso_unitario_kg(linea)

            # Regla enterprise:
            # - REAL en el eje oficial (cantidad o peso_kg)
            # - ESTIMADO en el eje derivado (peso_estimado_kg o cantidad_estimada)
            cantidad_real: float | None = None
            peso_real: float | None = None
            cantidad_est: float | None = None
            peso_est: float | None = None

            if modo == InboundLineaModo.CANTIDAD:
                if peso_parsed is not None:
                    raise InboundDomainError(
                        "Esta línea es por CANTIDAD: no ingreses Kg en el pallet. "
                        "El sistema calcula el estimado automáticamente según kg/u."
                    )
                if cantidad_parsed is None:
                    raise InboundDomainError("Esta línea es por CANTIDAD: debes ingresar cantidad (> 0).")

                cantidad_real = float(cantidad_parsed)

                if peso_unitario is None:
                    raise InboundDomainError(
                        "Esta línea es por CANTIDAD pero no tiene conversión configurada (kg/u). "
                        "Corrige el maestro del producto o el override de la línea."
                    )
                peso_est = _calc_kg_desde_cantidad(cantidad_real, float(peso_unitario))

                # Pendiente por cantidad (base documental)
                base = view.base_cantidad
                if base is None or base <= 0:
                    raise InboundDomainError("La línea no tiene cantidad base válida para asignar.")
                cant_asig, _kg_asig = _sum_asignado_por_linea_en_recepcion(
                    db,
                    negocio_id=negocio_id,
                    recepcion_id=recepcion_id,
                    linea_id=linea.id,
                )
                pend = float(base) - float(cant_asig)
                if cantidad_real > pend + _EPS:
                    raise InboundDomainError(f"Cantidad supera el pendiente. Pendiente: {max(pend, 0):.3f}")

            else:  # PESO
                if cantidad_parsed is not None:
                    raise InboundDomainError(
                        "Esta línea es por PESO: no ingreses Cantidad en el pallet. "
                        "El sistema deriva el estimado si existe kg/u."
                    )
                if peso_parsed is None:
                    raise InboundDomainError("Esta línea es por PESO: debes ingresar Kg (> 0).")

                peso_real = float(peso_parsed)

                # Estimado de cantidad si existe conversión
                if peso_unitario is not None:
                    cantidad_est = _calc_cantidad_desde_kg(peso_real, float(peso_unitario))

                # Pendiente por kg (base documental)
                base = view.base_peso_kg
                if base is None or base <= 0:
                    raise InboundDomainError("La línea no tiene peso base válido para asignar.")
                _cant_asig, kg_asig = _sum_asignado_por_linea_en_recepcion(
                    db,
                    negocio_id=negocio_id,
                    recepcion_id=recepcion_id,
                    linea_id=linea.id,
                )
                pend = float(base) - float(kg_asig)
                if peso_real > pend + _EPS:
                    raise InboundDomainError(f"Peso supera el pendiente. Pendiente: {max(pend, 0):.3f} kg")

            if cantidad_real is None and peso_real is None:
                raise InboundDomainError("Debes ingresar una cantidad o un peso mayor a cero.")

            item = InboundPalletItem(
                negocio_id=negocio_id,
                pallet_id=pallet.id,
                linea_id=linea.id,
                # REAL
                cantidad=(float(cantidad_real) if cantidad_real is not None else None),
                peso_kg=(float(peso_real) if peso_real is not None else None),
                # ESTIMADOS
                cantidad_estimada=(float(cantidad_est) if cantidad_est is not None else None),
                peso_estimado_kg=(float(peso_est) if peso_est is not None else None),
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(item)

            try:
                db.flush()
            except IntegrityError as exc:
                raise InboundDomainError("Esta línea ya está asignada a este pallet.") from exc

        # Si agregamos items, marcamos EN_PROCESO automáticamente (mejor UX)
        if pallet.estado == PalletEstado.ABIERTO:
            pallet.estado = PalletEstado.EN_PROCESO

        pallet.updated_at = utcnow()
        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo guardar el ítem. Verifica duplicados o datos inválidos.")


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
    pallet = obtener_pallet_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    item = (
        db.query(InboundPalletItem)
        .filter(
            InboundPalletItem.id == pallet_item_id,
            InboundPalletItem.pallet_id == pallet.id,
            InboundPalletItem.negocio_id == negocio_id,
        )
        .first()
    )
    if not item:
        raise InboundDomainError("Ítem no encontrado para este pallet.")

    db.delete(item)

    existe = db.query(InboundPalletItem.id).filter(InboundPalletItem.pallet_id == pallet.id).first()
    if not existe:
        pallet.estado = PalletEstado.ABIERTO

    pallet.updated_at = utcnow()
    db.commit()


# ============================
# Eliminar pallet
# ============================

def eliminar_pallet_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> None:
    pallet = obtener_pallet_editable(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    db.query(InboundPalletItem).filter(InboundPalletItem.pallet_id == pallet.id).delete()
    db.delete(pallet)
    db.commit()


# ============================
# Estado: listo / reabrir / bloquear
# ============================

def marcar_pallet_listo(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    user_id: int,
    *,
    reconciliar_soft: bool = True,
) -> None:
    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    _assert_pallet_no_bloqueado(pallet)

    if pallet.estado == PalletEstado.LISTO:
        return

    if not (pallet.observaciones or "").strip():
        raise InboundDomainError("Para marcar LISTO debes ingresar observaciones (mínimo algo descriptivo).")

    tiene_items = db.query(InboundPalletItem.id).filter(InboundPalletItem.pallet_id == pallet.id).first()
    if not tiene_items:
        raise InboundDomainError("No puedes marcar LISTO un pallet sin líneas asignadas.")

    pallet.estado = PalletEstado.LISTO
    pallet.cerrado_por_id = user_id
    pallet.cerrado_at = utcnow()
    pallet.updated_at = utcnow()

    if reconciliar_soft:
        _ = reconciliar_recepcion(
            db,
            negocio_id,
            recepcion_id,
            strict=False,
            require_editable=False,
            include_lineas=False,
            commit=False,
        )

    db.commit()


def reabrir_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> None:
    pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)
    _assert_pallet_no_bloqueado(pallet)

    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)

    # ✅ Enterprise: si tiene ítems, queda EN_PROCESO; si no, ABIERTO
    tiene_items = db.query(InboundPalletItem.id).filter(InboundPalletItem.pallet_id == pallet.id).first()
    pallet.estado = PalletEstado.EN_PROCESO if tiene_items else PalletEstado.ABIERTO

    pallet.cerrado_por_id = None
    pallet.cerrado_at = None
    pallet.updated_at = utcnow()
    db.commit()


def bloquear_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    *,
    motivo: str | None = None,
) -> None:
    _ = obtener_recepcion_editable(db, recepcion_id, negocio_id)
    pallet = obtener_pallet_seguro(db, negocio_id=negocio_id, recepcion_id=recepcion_id, pallet_id=pallet_id)

    if pallet.estado == PalletEstado.BLOQUEADO:
        return

    pallet.estado = PalletEstado.BLOQUEADO
    if motivo:
        obs = (pallet.observaciones or "").strip()
        add = f"[BLOQUEADO] {motivo.strip()}"
        pallet.observaciones = (obs + "\n" + add).strip() if obs else add

    pallet.updated_at = utcnow()
    db.commit()


# ============================
# Resumen para UI (lista)
# ============================

def construir_resumen_pallets(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> dict[int, dict[str, float]]:
    """
    Devuelve resumen por pallet:
    - n_items
    - cant: suma(coalesce(cantidad, cantidad_estimada))
    - kg:   suma(coalesce(peso_kg, peso_estimado_kg))

    Esto mantiene la UI coherente incluso si el eje real va en None
    (porque guardamos estimados en columnas separadas).
    """
    rows = (
        db.query(
            InboundPalletItem.pallet_id.label("pallet_id"),
            func.count(InboundPalletItem.id).label("n_items"),
            func.coalesce(
                func.sum(func.coalesce(InboundPalletItem.cantidad, InboundPalletItem.cantidad_estimada)), 0.0
            ).label("cant"),
            func.coalesce(
                func.sum(func.coalesce(InboundPalletItem.peso_kg, InboundPalletItem.peso_estimado_kg)), 0.0
            ).label("kg"),
        )
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
        )
        .group_by(InboundPalletItem.pallet_id)
        .all()
    )

    out: dict[int, dict[str, float]] = {}
    for r in rows:
        out[int(r.pallet_id)] = {
            "n_items": float(r.n_items or 0),
            "cant": float(r.cant or 0.0),
            "kg": float(r.kg or 0.0),
        }
    return out
