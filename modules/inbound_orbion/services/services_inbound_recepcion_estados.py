# modules/inbound_orbion/services/services_inbound_recepcion_estados #
from __future__ import annotations

from datetime import datetime
from typing import Dict

from sqlalchemy.orm import Session
from sqlalchemy import func

from core.models.time import utcnow
from core.models.enums import RecepcionEstado
from core.models.inbound.recepciones import InboundRecepcion
from core.models.inbound.lineas import InboundLinea
from core.models.inbound.pallets import InboundPallet, InboundPalletItem
from modules.inbound_orbion.services.services_inbound_citas import sync_cita_desde_recepcion


from modules.inbound_orbion.services.services_inbound_core import InboundDomainError

# ✅ Contrato oficial de línea (enterprise)
from modules.inbound_orbion.services.inbound_linea_contract import (
    normalizar_linea,
    InboundLineaContractError,
)


# =========================================================
# HELPERS
# =========================================================

def _minutos_entre(a: datetime | None, b: datetime | None) -> float | None:
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 60.0, 2)


def _calcular_metrics(r: InboundRecepcion) -> Dict[str, float | None]:
    """
    KPIs base inbound:
    - espera: arribo -> inicio descarga
    - descarga: inicio -> fin
    - control_calidad: fin -> cierre
    - total_hasta_fin_descarga: arribo -> fin
    - total_hasta_cierre: arribo -> cierre
    """
    return {
        "tiempo_espera_min": _minutos_entre(r.fecha_arribo, r.fecha_inicio_descarga),
        "tiempo_descarga_min": _minutos_entre(r.fecha_inicio_descarga, r.fecha_fin_descarga),
        "tiempo_control_calidad_min": _minutos_entre(r.fecha_fin_descarga, r.fecha_cierre),
        "tiempo_total_hasta_fin_descarga_min": _minutos_entre(r.fecha_arribo, r.fecha_fin_descarga),
        "tiempo_total_hasta_cierre_min": _minutos_entre(r.fecha_arribo, r.fecha_cierre),
    }


def _obtener_recepcion_segura(db: Session, negocio_id: int, recepcion_id: int) -> InboundRecepcion:
    r = db.get(InboundRecepcion, recepcion_id)
    if not r or int(r.negocio_id) != int(negocio_id):
        raise InboundDomainError("Recepción no encontrada.")
    return r


def _assert_recepcion_con_documento(r: InboundRecepcion) -> None:
    doc = (getattr(r, "documento_ref", None) or "").strip()
    if not doc:
        raise InboundDomainError(
            "No puedes cerrar una recepción sin Documento Ref (guía/factura/BL/OC)."
        )


def _assert_recepcion_con_lineas(db: Session, negocio_id: int, recepcion_id: int) -> None:
    any_linea = (
        db.query(InboundLinea.id)
        .filter(InboundLinea.negocio_id == negocio_id, InboundLinea.recepcion_id == recepcion_id)
        .first()
    )
    if not any_linea:
        raise InboundDomainError("No puedes cerrar una recepción sin líneas.")


def _assert_recepcion_con_pallets_y_items(db: Session, negocio_id: int, recepcion_id: int) -> None:
    any_pallet = (
        db.query(InboundPallet.id)
        .filter(InboundPallet.negocio_id == negocio_id, InboundPallet.recepcion_id == recepcion_id)
        .first()
    )
    if not any_pallet:
        raise InboundDomainError("No puedes cerrar una recepción sin pallets.")

    any_item = (
        db.query(InboundPalletItem.id)
        .join(InboundPallet, InboundPallet.id == InboundPalletItem.pallet_id)
        .filter(
            InboundPallet.negocio_id == negocio_id,
            InboundPallet.recepcion_id == recepcion_id,
            InboundPalletItem.negocio_id == negocio_id,
        )
        .first()
    )
    if not any_item:
        raise InboundDomainError("No puedes cerrar una recepción con pallets vacíos (sin ítems).")


def _validar_contrato_lineas_strict(db: Session, negocio_id: int, recepcion_id: int) -> None:
    """
    Enterprise: todas las líneas deben cumplir contrato oficial.
    - deben tener objetivo doc (cantidad_documento o peso_kg)
    - modo consistente (cantidad o peso)
    """
    lineas = (
        db.query(InboundLinea)
        .filter(InboundLinea.negocio_id == negocio_id, InboundLinea.recepcion_id == recepcion_id)
        .all()
    )

    errores: list[str] = []
    for ln in lineas:
        try:
            # allow_draft=False => enterprise strict
            normalizar_linea(ln, allow_draft=False)
        except InboundLineaContractError as e:
            # junta errores, no rompe a la primera -> mejor UX
            nombre = getattr(getattr(ln, "producto", None), "nombre", None) or f"Linea #{ln.id}"
            errores.append(f"{nombre}: {str(e)}")

    if errores:
        msg = "No se puede cerrar: hay líneas con contrato incompleto/inconsistente.\n- " + "\n- ".join(errores[:8])
        if len(errores) > 8:
            msg += f"\n(+{len(errores) - 8} más)"
        raise InboundDomainError(msg)


def _reconciliar_recepcion(db: Session, negocio_id: int, recepcion_id: int) -> None:
    """
    Ejecuta reconciliación (recalcular recibidos) previo al cierre.
    Import local para evitar ciclos.
    """
    from modules.inbound_orbion.services.services_inbound_reconciliacion import reconciliar_recepcion

    reconciliar_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)


# =========================================================
# TRANSICIONES (workflow)
# =========================================================
# PRE_REGISTRADO -> EN_ESPERA -> EN_DESCARGA -> EN_CONTROL_CALIDAD -> CERRADO

ACCIONES = {
    "marcar_en_espera": "marcar_en_espera",
    "iniciar_descarga": "iniciar_descarga",
    "finalizar_descarga": "finalizar_descarga",
    "cerrar_recepcion": "cerrar_recepcion",
}


def aplicar_accion_estado(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    accion: str,
) -> InboundRecepcion:
    r = _obtener_recepcion_segura(db, negocio_id, recepcion_id)

    # Ultra enterprise: cierre y cancelado
    if r.estado in (RecepcionEstado.CERRADO, RecepcionEstado.CANCELADO):
        raise InboundDomainError("La recepción está cerrada/cancelada. No se permiten cambios de estado.")


    now = utcnow()

    if accion == "marcar_en_espera":
        if r.estado != RecepcionEstado.PRE_REGISTRADO:
            raise InboundDomainError("Acción inválida para el estado actual.")
        r.estado = RecepcionEstado.EN_ESPERA
        r.fecha_arribo = r.fecha_arribo or now
        r.fecha_recepcion = r.fecha_recepcion or now

    elif accion == "iniciar_descarga":
        if r.estado not in (RecepcionEstado.PRE_REGISTRADO, RecepcionEstado.EN_ESPERA):
            raise InboundDomainError("Acción inválida para el estado actual.")
        r.estado = RecepcionEstado.EN_DESCARGA
        r.fecha_arribo = r.fecha_arribo or now
        r.fecha_recepcion = r.fecha_recepcion or now
        r.fecha_inicio_descarga = r.fecha_inicio_descarga or now

    elif accion == "finalizar_descarga":
        if r.estado != RecepcionEstado.EN_DESCARGA:
            raise InboundDomainError("Acción inválida para el estado actual.")
        r.estado = RecepcionEstado.EN_CONTROL_CALIDAD
        r.fecha_inicio_descarga = r.fecha_inicio_descarga or now
        r.fecha_fin_descarga = r.fecha_fin_descarga or now

    elif accion == "cerrar_recepcion":
        # Solo desde control calidad
        if r.estado != RecepcionEstado.EN_CONTROL_CALIDAD:
            raise InboundDomainError("Acción inválida para el estado actual.")

        # ✅ Enterprise hard rules (no UI block; service valida)
        _assert_recepcion_con_documento(r)
        _assert_recepcion_con_lineas(db, negocio_id, recepcion_id)
        _assert_recepcion_con_pallets_y_items(db, negocio_id, recepcion_id)

        # ✅ 1) reconciliar (fuente de verdad = pallets)
        _reconciliar_recepcion(db, negocio_id, recepcion_id)

        # ✅ 2) validar contrato oficial de líneas (strict)
        _validar_contrato_lineas_strict(db, negocio_id, recepcion_id)

        # ✅ 3) cerrar
        r.estado = RecepcionEstado.CERRADO
        r.fecha_fin_descarga = r.fecha_fin_descarga or now
        r.fecha_cierre = r.fecha_cierre or now

    else:
        raise InboundDomainError("Acción no reconocida.")
    # ✅ Sync cita (1:1) desde estado de recepción
    sync_cita_desde_recepcion(db, r)
    db.commit()
    db.refresh(r)
    return r


def obtener_metrics_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> Dict[str, float | None]:
    r = _obtener_recepcion_segura(db, negocio_id, recepcion_id)
    return _calcular_metrics(r)
