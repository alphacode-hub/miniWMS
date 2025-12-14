# modules/inbound_orbion/services/services_inbound_pallets.py
"""
Servicios – Pallets Inbound (ORBION)

✔ Multi-tenant estricto (negocio_id + recepcion_id)
✔ Transacciones consistentes (1 commit por operación)
✔ Validaciones enterprise (pendientes por línea, no duplicados, estados)
✔ Compatible con split de modelos (core.models.*)
✔ Timestamps UTC timezone-aware (usa utcnow() si existe en core)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import InboundLinea, InboundPallet, InboundPalletItem
from .services_inbound_core import (
    InboundConfig,
    InboundDomainError,
    normalizar_estado_recepcion,
    obtener_recepcion_segura,
    validar_recepcion_editable,
)

# ============================
#   HELPERS
# ============================


def _utcnow() -> datetime:
    # Evitamos depender de core.models.utcnow para que este servicio sea estable.
    return datetime.now(timezone.utc)


def _calcular_peso_neto(
    peso_bruto_kg: float | None,
    peso_tara_kg: float | None,
) -> float | None:
    if peso_bruto_kg is None or peso_tara_kg is None:
        return None
    return round(float(peso_bruto_kg) - float(peso_tara_kg), 3)


def _to_positive_float(v: Any) -> float | None:
    """
    Normaliza a float > 0, si no -> None.
    Tolera strings con coma. Lanza InboundDomainError si es inválido.
    """
    if v is None:
        return None

    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
        v = v.replace(",", ".")

    try:
        n = float(v)
    except (TypeError, ValueError) as exc:
        raise InboundDomainError(
            "Valor numérico inválido. Usa números (ej: 10 o 10.5)."
        ) from exc

    return n if n > 0 else None


def _get_linea_kg_base(linea: InboundLinea) -> float | None:
    """
    Determina el 'kilos base' de la línea de manera tolerante a cambios de modelo.

    Preferencia:
    - linea.kilos (si existe)
    - linea.peso_kg (si existe)
    - linea.peso_total_kg (si existe)
    """
    for attr in ("kilos", "peso_kg", "peso_total_kg"):
        if hasattr(linea, attr):
            v = getattr(linea, attr)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
    return None


def _get_linea_cantidad_base(linea: InboundLinea) -> float | None:
    """
    Cantidad base canónica:
    - cantidad_recibida si existe, si no cantidad_esperada
    """
    v = getattr(linea, "cantidad_recibida", None)
    if v is None:
        v = getattr(linea, "cantidad_esperada", None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _assert_pallet_editable(pallet: InboundPallet) -> None:
    """
    Si el modelo tiene estado, evitamos mutar pallets LISTO/BLOQUEADO.
    """
    if hasattr(pallet, "estado") and pallet.estado:
        est = str(pallet.estado).strip().upper()
        if est in {"LISTO", "BLOQUEADO"}:
            raise InboundDomainError("Este pallet no se puede modificar porque ya está cerrado o bloqueado.")


def _sum_asignado_por_linea_en_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    linea_id: int,
) -> tuple[float, float]:
    """
    Suma asignada a nivel recepción (entre todos los pallets) para una línea.
    Retorna (cantidad_asignada, kg_asignado).
    """
    asign = (
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
    cant_asig = float(asign[0] or 0)
    kg_asig = float(asign[1] or 0)
    return cant_asig, kg_asig


# ============================
#   CREAR PALLET
# ============================


def crear_pallet_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    codigo_pallet: str,
    peso_bruto_kg: float | None = None,
    peso_tara_kg: float | None = None,
    bultos: int | None = None,
    temperatura_promedio: float | None = None,
    observaciones: str | None = None,
    creado_por_id: int | None = None,
) -> InboundPallet:
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    # Workflow: no crear si recepción no editable
    cfg = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    codigo_norm = (codigo_pallet or "").strip().upper()
    if not codigo_norm:
        raise InboundDomainError("El código del pallet es obligatorio.")

    existe = (
        db.query(InboundPallet.id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion.id,
            InboundPallet.codigo_pallet == codigo_norm,
        )
        .first()
    )
    if existe:
        raise InboundDomainError(f"El pallet con código '{codigo_norm}' ya existe en esta recepción.")

    pallet = InboundPallet(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        codigo_pallet=codigo_norm,
        peso_bruto_kg=peso_bruto_kg,
        peso_tara_kg=peso_tara_kg,
        peso_neto_kg=_calcular_peso_neto(peso_bruto_kg, peso_tara_kg),
        bultos=bultos,
        temperatura_promedio=temperatura_promedio,
        observaciones=(observaciones or "").strip() or None,
        creado_por_id=creado_por_id,
        creado_en=_utcnow(),
    )

    db.add(pallet)
    db.commit()
    db.refresh(pallet)
    return pallet


# ============================
#   AGREGAR ITEMS A PALLET
# ============================


def agregar_items_a_pallet(
    db: Session,
    negocio_id: int,
    pallet_id: int,
    items: Iterable[dict[str, Any]],
) -> None:
    """
    Agrega items al pallet en una única transacción.

    Reglas enterprise:
    - Pallet pertenece al negocio y recepción.
    - Recepción editable (según workflow).
    - Pallet editable (si está LISTO/BLOQUEADO no se modifica).
    - No duplicar línea dentro del mismo pallet (enforced por UniqueConstraint y por validación).
    - No sobre-asignar cantidad/peso vs base de la línea (pendiente a nivel recepción).
    """
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, pallet.recepcion_id, negocio_id)
    cfg = InboundConfig.from_negocio(db, negocio_id)
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

            linea = db.get(InboundLinea, linea_id)
            if not linea:
                raise InboundDomainError(f"Línea inbound {linea_id} no encontrada.")
            if linea.recepcion_id != recepcion.id:
                raise InboundDomainError(f"La línea {linea_id} no pertenece a esta recepción.")

            # Normalización de cantidad/peso
            cantidad = _to_positive_float(item_data.get("cantidad"))
            peso_kg = _to_positive_float(item_data.get("peso_kg"))

            if cantidad is None and peso_kg is None:
                raise InboundDomainError("Debes ingresar una cantidad o un peso mayor a cero.")

            # No duplicar misma línea dentro del mismo pallet (regla enterprise)
            dup = (
                db.query(InboundPalletItem.id)
                .filter(
                    InboundPalletItem.pallet_id == pallet.id,
                    InboundPalletItem.linea_id == linea.id,
                )
                .first()
            )
            if dup:
                raise InboundDomainError(f"La línea {linea_id} ya está asignada a este pallet.")

            # Bases canónicas de la línea
            cant_base = _get_linea_cantidad_base(linea)
            kg_base = _get_linea_kg_base(linea)

            # Asignado en toda la recepción (todos los pallets)
            cant_asig, kg_asig = _sum_asignado_por_linea_en_recepcion(
                db,
                negocio_id=negocio_id,
                recepcion_id=recepcion.id,
                linea_id=linea.id,
            )

            # Validación enterprise (cantidad)
            if cantidad is not None:
                if cant_base is None:
                    raise InboundDomainError("Esta línea no tiene cantidad base para asignar.")
                cant_pend = float(cant_base) - float(cant_asig)
                if cantidad > cant_pend + 1e-9:
                    raise InboundDomainError(
                        f"Cantidad supera el pendiente. Pendiente: {max(cant_pend, 0):.3f}"
                    )

            # Validación enterprise (peso)
            if peso_kg is not None:
                if kg_base is None:
                    raise InboundDomainError(
                        "Esta línea no tiene kilos base para asignar. "
                        "Completa el campo de kilos/peso en la línea."
                    )
                kg_pend = float(kg_base) - float(kg_asig)
                if peso_kg > kg_pend + 1e-9:
                    raise InboundDomainError(
                        f"Peso supera el pendiente. Pendiente: {max(kg_pend, 0):.3f} kg"
                    )

            item = InboundPalletItem(
                pallet_id=pallet.id,
                linea_id=linea.id,
                cantidad=cantidad,
                peso_kg=peso_kg,
            )
            db.add(item)

            # Forzar constraint uq (pallet_id, linea_id) lo antes posible
            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise InboundDomainError(
                    "Esta línea ya está asignada a este pallet. Refresca y prueba con otra línea."
                ) from exc

        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise exc


# ============================
#   LISTAR ITEMS DE PALLET
# ============================


def listar_items_de_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> list[InboundPalletItem]:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")
    if pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("El pallet no pertenece a esta recepción.")

    return (
        db.query(InboundPalletItem)
        .filter(InboundPalletItem.pallet_id == pallet.id)
        .order_by(InboundPalletItem.id.asc())
        .all()
    )


# ============================
#   QUITAR ITEM DE PALLET
# ============================


def quitar_item_de_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    pallet_item_id: int,
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")
    if pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("El pallet no pertenece a esta recepción.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    item = db.get(InboundPalletItem, pallet_item_id)
    if not item or item.pallet_id != pallet.id:
        raise InboundDomainError("Ítem no encontrado para este pallet.")

    db.delete(item)
    db.commit()


# ============================
#   ELIMINAR PALLET
# ============================


def eliminar_pallet_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> None:
    pallet = db.get(InboundPallet, pallet_id)
    if not pallet or pallet.negocio_id != negocio_id:
        raise InboundDomainError("Pallet inbound no encontrado para este negocio.")
    if pallet.recepcion_id != recepcion_id:
        raise InboundDomainError("El pallet no pertenece a esta recepción.")

    _assert_pallet_editable(pallet)

    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    db.query(InboundPalletItem).filter(InboundPalletItem.pallet_id == pallet.id).delete()
    db.delete(pallet)
    db.commit()


# ============================
#   MARCAR PALLET LISTO
# ============================


def marcar_pallet_listo(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
    user_id: int,
) -> None:
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
    cfg = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    obs = (pallet.observaciones or "").strip()
    if len(obs) < 5:
        raise InboundDomainError("Para cerrar el pallet debes ingresar una observación mínima (>= 5 caracteres).")

    tiene_items = (
        db.query(InboundPalletItem.id)
        .filter(InboundPalletItem.pallet_id == pallet.id)
        .first()
    )
    if not tiene_items:
        raise InboundDomainError("No puedes cerrar un pallet sin líneas asignadas.")

    # Estado enterprise
    if hasattr(pallet, "estado"):
        pallet.estado = "LISTO"
    pallet.cerrado_por_id = user_id
    pallet.cerrado_en = _utcnow()
    db.commit()


# ============================
#   REABRIR PALLET
# ============================


def reabrir_pallet(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    pallet_id: int,
) -> None:
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
    cfg = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)

    # Si estaba bloqueado, reabrir debería requerir rol/permiso (se valida en routes/dep)
    if hasattr(pallet, "estado"):
        pallet.estado = "EN_PROCESO"
    pallet.cerrado_por_id = None
    pallet.cerrado_en = None
    db.commit()
