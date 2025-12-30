# modules/inbound_orbion/services/services_inbound_recepciones_cierre.py
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from core.models.inbound.recepciones import InboundRecepcion
from core.models.inbound.lineas import InboundLinea

from modules.inbound_orbion.services.services_inbound_recepciones_bridge import (
    cerrar_recepcion_bridge,
)

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_config_inbound,
    validar_recepcion_editable,
)

from modules.inbound_orbion.services.services_inbound_reconciliacion import (
    reconciliar_recepcion,
)

from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)


# ============================================================
#   Helpers (opcional) - Preflight UX (no duplicar "verdad")
# ============================================================

def _has_text(v: Any) -> bool:
    return bool((v or "").strip())


def _validar_documento_minimo(recepcion: InboundRecepcion) -> None:
    # Preflight UX: mensaje claro antes de ejecutar cierre
    if not _has_text(getattr(recepcion, "documento_ref", None)):
        raise InboundDomainError(
            "No puedes cerrar la recepción sin documento de respaldo (documento_ref)."
        )


def _validar_lineas_con_documento(lineas: List[InboundLinea]) -> None:
    """
    Preflight UX: valida que existan líneas y tengan objetivos doc.
    OJO: el "motor oficial" (aplicar_accion_estado/cerrar_recepcion) también valida.
    Esto es solo para entregar errores más amigables antes de correr procesos.
    """
    if not lineas:
        raise InboundDomainError("No puedes cerrar la recepción sin líneas.")

    errores: List[str] = []
    for ln in lineas:
        doc_qty = getattr(ln, "cantidad_documento", None)
        doc_kg = getattr(ln, "peso_kg", None)

        if doc_qty is None and doc_kg is None:
            errores.append(
                f"Línea #{ln.id}: falta objetivo de documento (cantidad_documento o peso_kg)."
            )
            continue

        if doc_qty is not None:
            try:
                if float(doc_qty) <= 0:
                    errores.append(f"Línea #{ln.id}: cantidad_documento debe ser > 0.")
            except Exception:
                errores.append(f"Línea #{ln.id}: cantidad_documento inválida.")

        if doc_kg is not None:
            try:
                if float(doc_kg) <= 0:
                    errores.append(f"Línea #{ln.id}: peso_kg (doc) debe ser > 0.")
            except Exception:
                errores.append(f"Línea #{ln.id}: peso_kg (doc) inválido.")

    if errores:
        msg = "No puedes cerrar: corrige estas líneas:\n- " + "\n- ".join(errores[:12])
        if len(errores) > 12:
            msg += f"\n- … y {len(errores) - 12} más."
        raise InboundDomainError(msg)


def _validar_listo_para_cierre(
    db: Session,
    *,
    negocio_id: int,
    recepcion: InboundRecepcion,
) -> None:
    """
    Preflight (UX). La verdad final está en el motor oficial.
    """
    _validar_documento_minimo(recepcion)

    lineas = (
        db.query(InboundLinea)
        .filter(
            InboundLinea.negocio_id == negocio_id,
            InboundLinea.recepcion_id == recepcion.id,
        )
        .order_by(InboundLinea.id.asc())
        .all()
    )

    _validar_lineas_con_documento(lineas)


# ============================================================
#   API: Cerrar (delegando a la ÚNICA verdad)
# ============================================================

def reconciliar_y_cerrar_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    user_id: int,
    user_email: str,
) -> Dict[str, Any]:
    """
    Servicio "UI-friendly" para cerrar una recepción.
    La ÚNICA verdad de cierre vive en:
      cerrar_recepcion_bridge() -> aplicar_accion_estado(..., "cerrar_recepcion")

    Este service:
      1) valida recepción segura
      2) valida editable (config/estado)
      3) (opcional) preflight UX
      4) delega cierre al bridge (verdad)
      5) log + respuesta
    """
    # 1) Obtener recepción segura
    try:
        recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_cierre_not_found",
            negocio_id=negocio_id,
            user_email=user_email,
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise

    # 2) Validar editable por config/estado
    cfg = obtener_config_inbound(db, negocio_id)
    try:
        validar_recepcion_editable(recepcion, cfg)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_cierre_not_editable",
            negocio_id=negocio_id,
            user_email=user_email,
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise

    # 3) Preflight UX (opcional, NO duplica commits ni estados)
    try:
        _validar_listo_para_cierre(db, negocio_id=negocio_id, recepcion=recepcion)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_cierre_validation_failed",
            negocio_id=negocio_id,
            user_email=user_email,
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise

    # 4) ÚNICA VERDAD: cerrar (incluye reconciliación + estado + timestamps + sync cita + commit)
    try:
        recepcion_cerrada = cerrar_recepcion_bridge(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
        )
    except InboundDomainError as e:
        # El motor ya arma mensajes enterprise; solo registramos
        log_inbound_error(
            "recepcion_cierre_bridge_failed",
            negocio_id=negocio_id,
            user_email=user_email,
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise

    log_inbound_event(
        "recepcion_cerrada",
        negocio_id=negocio_id,
        user_email=user_email,
        recepcion_id=recepcion_id,
        user_id=user_id,
        estado=str(getattr(recepcion_cerrada, "estado", "")),
        fecha_cierre=str(getattr(recepcion_cerrada, "fecha_cierre", None)),
    )

    # Nota: si quieres "resumen" para UI, lo más seguro es que la UI recargue la vista.
    # Evitamos recalcular reconciliación aquí para no duplicar escritura.
    return {
        "ok": True,
        "recepcion_id": recepcion_cerrada.id,
        "estado": str(getattr(recepcion_cerrada, "estado", "")),
        "fecha_cierre": str(getattr(recepcion_cerrada, "fecha_cierre", None)),
    }


# ============================================================
#   API: Reconciliar sin cerrar (botón recalcular)
# ============================================================

def reconciliar_sin_cerrar(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    user_id: int,
    user_email: str,
) -> Dict[str, Any]:
    """
    Reconciliación enterprise sin cierre.
    Útil para botón "Recalcular pendientes" sin bloquear nada.
    """
    try:
        recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
        cfg = obtener_config_inbound(db, negocio_id)
        validar_recepcion_editable(recepcion, cfg)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_reconciliar_failed",
            negocio_id=negocio_id,
            user_email=user_email,
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise

    resumen = reconciliar_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    log_inbound_event(
        "recepcion_reconciliada_sin_cierre",
        negocio_id=negocio_id,
        user_email=user_email,
        recepcion_id=recepcion_id,
        user_id=user_id,
        lineas_total=resumen.get("lineas_total"),
    )

    return {
        "ok": True,
        "recepcion_id": recepcion_id,
        "reconciliacion": resumen,
    }
