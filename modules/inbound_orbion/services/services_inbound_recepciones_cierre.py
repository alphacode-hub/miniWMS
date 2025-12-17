# modules/inbound_orbion/services/services_inbound_recepciones_cierre.py
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from core.models.inbound.recepciones import InboundRecepcion
from core.models.inbound.lineas import InboundLinea

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_config_inbound,
    validar_recepcion_editable,
)

from modules.inbound_orbion.services.services_inbound_reconciliacion import (
    reconciliar_recepcion,
)

# Si tienes logger enterprise, úsalo
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)


# ============================================================
#   Helpers de contrato enterprise (cierre)
# ============================================================

def _has_text(v: Any) -> bool:
    return bool((v or "").strip())

def _validar_documento_minimo(recepcion: InboundRecepcion) -> None:
    # Enterprise: sin documento_ref no hay recepción cerrable
    if not _has_text(getattr(recepcion, "documento_ref", None)):
        raise InboundDomainError("No puedes cerrar la recepción sin documento de respaldo (documento_ref).")

def _validar_lineas_con_documento(lineas: List[InboundLinea]) -> None:
    """
    Enterprise v1 (cierre):
      - Debe existir al menos 1 línea.
      - Cada línea debe tener objetivo de documento: cantidad_documento o peso_kg.
      - Los objetivos deben ser > 0 si existen.
    """
    if not lineas:
        raise InboundDomainError("No puedes cerrar la recepción sin líneas.")

    errores: List[str] = []
    for ln in lineas:
        doc_qty = getattr(ln, "cantidad_documento", None)
        doc_kg = getattr(ln, "peso_kg", None)

        if doc_qty is None and doc_kg is None:
            errores.append(f"Línea #{ln.id}: falta objetivo de documento (cantidad_documento o peso_kg).")
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
        # Devolvemos un mensaje legible (sin romper UI)
        msg = "No puedes cerrar: corrige estas líneas:\n- " + "\n- ".join(errores[:12])
        if len(errores) > 12:
            msg += f"\n- … y {len(errores) - 12} más."
        raise InboundDomainError(msg)

def _validar_listo_para_cierre(
    db: Session,
    negocio_id: int,
    recepcion: InboundRecepcion,
) -> List[InboundLinea]:
    """
    Valida precondiciones enterprise para cerrar.
    NO bloquea captura diaria; solo bloquea el acto de cerrar.
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
    return lineas


# ============================================================
#   API principal: Reconciliar + Cerrar
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
    ULTRA ENTERPRISE v1:
      1) Valida recepción editable (config/estado)
      2) Exige documento_ref (no se cierra sin respaldo)
      3) Exige líneas con objetivo doc (cantidad_documento o peso_kg)
      4) Ejecuta reconciliación: escribe físico real en lineas
      5) Marca recepción como cerrada (fecha_cierre) y/o estado enterprise
      6) Devuelve resumen listo para UI/auditoría
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

    # 3) Validaciones enterprise (solo para cerrar)
    try:
        _validar_listo_para_cierre(db, negocio_id, recepcion)
    except InboundDomainError as e:
        log_inbound_error(
            "recepcion_cierre_validation_failed",
            negocio_id=negocio_id,
            user_email=user_email,
            recepcion_id=recepcion_id,
            error=e.message,
        )
        raise

    # 4) Reconciliar (escribe físico real + diferencias + estados)
    resumen = reconciliar_recepcion(db=db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    # 5) Marcar cierre formal (sin inventario aún, eso es fase siguiente)
    # - Tu modelo ya tiene fecha_cierre
    # - Si además tienes estado enum, puedes setearlo aquí
    from core.models.time import utcnow

    recepcion.fecha_cierre = utcnow()

    # Si existe estado en tu enum (ej: CERRADA/RECONCILIADA), setéalo sin romper baseline:
    if hasattr(recepcion, "estado") and recepcion.estado is not None:
        try:
            # Intento suave: si el enum tiene "CERRADA" o "RECONCILIADA"
            # (no asumimos nombres exactos para no romper)
            estado_enum = type(recepcion.estado)
            for candidate in ("RECONCILIADA", "CERRADA", "CERRADO"):
                if hasattr(estado_enum, candidate):
                    recepcion.estado = getattr(estado_enum, candidate)
                    break
        except Exception:
            pass

    db.commit()
    db.refresh(recepcion)

    log_inbound_event(
        "recepcion_reconciliada_y_cerrada",
        negocio_id=negocio_id,
        user_email=user_email,
        recepcion_id=recepcion_id,
        user_id=user_id,
        lineas_total=resumen.get("lineas_total"),
        resumen_estados=resumen.get("resumen_estados"),
    )

    return {
        "ok": True,
        "recepcion_id": recepcion.id,
        "fecha_cierre": str(getattr(recepcion, "fecha_cierre", None)),
        "reconciliacion": resumen,
    }


def reconciliar_sin_cerrar(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    user_id: int,
    user_email: str,
) -> Dict[str, Any]:
    """
    Reconciliación enterprise sin gate de cierre.
    Útil para botón 'Recalcular pendientes' sin bloquear nada.
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
    return {"ok": True, "recepcion_id": recepcion_id, "reconciliacion": resumen}
