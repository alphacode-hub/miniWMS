# modules/inbound_orbion/services/services_inbound_recepcion_estados.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models.enums import RecepcionEstado
from core.models.inbound.recepciones import InboundRecepcion

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError

# ✅ Sync oficial (cita <- recepción)
from modules.inbound_orbion.services.services_inbound_citas_sync import (
    sync_cita_desde_recepcion,
)


# ============================
# MÉTRICAS (DTO)
# ============================

@dataclass
class RecepcionMetrics:
    # ✅ Minutos ENTEROS para UI (coherentes entre sí)
    tiempo_espera_min: int | None = None
    tiempo_descarga_min: int | None = None
    tiempo_total_hasta_fin_descarga_min: int | None = None


# ============================
# Helpers de tiempo
# ============================

def _seconds_between(a: datetime | None, b: datetime | None) -> float | None:
    """
    Retorna segundos (float) entre a -> b.
    - None si falta algún timestamp
    - 0 si delta es negativo (defensivo)
    """
    if not a or not b:
        return None
    try:
        delta = b - a
        sec = float(delta.total_seconds())
        return sec if sec >= 0 else 0.0
    except Exception:
        return None


def _to_minutes_int(seconds: float | None) -> int | None:
    """
    Regla única para pasar segundos -> minutos ENTEROS.
    Usamos redondeo al minuto más cercano (enterprise-friendly).
    """
    if seconds is None:
        return None
    try:
        m = int(round(seconds / 60.0))
        return m if m >= 0 else 0
    except Exception:
        return None


# ============================
# MÉTRICAS (PUBLIC)
# ============================

def obtener_metrics_recepcion(db: Session, *, negocio_id: int, recepcion_id: int) -> RecepcionMetrics:
    """
    Metrics UI-friendly para inbound_recepcion_detalle.html

    - tiempo_espera_min: fecha_arribo -> fecha_inicio_descarga
    - tiempo_descarga_min: fecha_inicio_descarga -> fecha_fin_descarga
    - tiempo_total_hasta_fin_descarga_min:
        ✅ si existen espera y descarga: total = espera + descarga (coherencia matemática en UI)
        ✅ si falta un tramo: total = arribo -> fin (si existe)
    """
    r = db.get(InboundRecepcion, int(recepcion_id))
    if not r or int(getattr(r, "negocio_id", 0)) != int(negocio_id):
        raise InboundDomainError("Recepción no encontrada.")

    fecha_arribo = getattr(r, "fecha_arribo", None)
    fecha_inicio_descarga = getattr(r, "fecha_inicio_descarga", None)
    fecha_fin_descarga = getattr(r, "fecha_fin_descarga", None)

    espera_sec = _seconds_between(fecha_arribo, fecha_inicio_descarga)
    descarga_sec = _seconds_between(fecha_inicio_descarga, fecha_fin_descarga)
    total_sec = _seconds_between(fecha_arribo, fecha_fin_descarga)

    espera_min = _to_minutes_int(espera_sec)
    descarga_min = _to_minutes_int(descarga_sec)

    # ✅ Total coherente: si puedo sumar tramos, el total mostrado debe calzar con la suma mostrada.
    if espera_min is not None and descarga_min is not None:
        total_min = espera_min + descarga_min
    else:
        total_min = _to_minutes_int(total_sec)

    return RecepcionMetrics(
        tiempo_espera_min=espera_min,
        tiempo_descarga_min=descarga_min,
        tiempo_total_hasta_fin_descarga_min=total_min,
    )


# ============================
# WORKFLOW / ESTADOS
# ============================

def _ensure_owned(r: InboundRecepcion, negocio_id: int) -> None:
    if not r or int(getattr(r, "negocio_id", 0)) != int(negocio_id):
        raise InboundDomainError("Recepción no encontrada.")


def aplicar_accion_estado(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    accion: str,
) -> InboundRecepcion:
    """
    Enterprise rule:
    - Cambia estado de recepción
    - Setea timestamps sin pisar si ya existen
    - Sincroniza estado de CITA desde RECEPCIÓN
    - ✅ Un solo commit (recepción + cita) = consistencia real
    """
    r = db.get(InboundRecepcion, int(recepcion_id))
    if not r:
        raise InboundDomainError("Recepción no encontrada.")
    _ensure_owned(r, negocio_id)

    now = utcnow()
    estado_actual = r.estado

    # -----------------------------------------
    # Transiciones válidas (enterprise)
    # -----------------------------------------

    if accion == "marcar_en_espera":
        # Solo desde PRE_REGISTRADO
        if estado_actual != RecepcionEstado.PRE_REGISTRADO:
            raise InboundDomainError("Solo puedes marcar arribado desde PRE_REGISTRADO.")

        if getattr(r, "fecha_arribo", None) is None:
            r.fecha_arribo = now

        if getattr(r, "fecha_recepcion", None) is None:
            r.fecha_recepcion = now

        r.estado = RecepcionEstado.EN_ESPERA

    elif accion == "iniciar_descarga":
        # Permitimos desde PRE_REGISTRADO o EN_ESPERA
        if estado_actual not in (RecepcionEstado.PRE_REGISTRADO, RecepcionEstado.EN_ESPERA):
            raise InboundDomainError("Solo puedes iniciar descarga desde PRE_REGISTRADO o EN_ESPERA.")

        # Si saltaron arribo, lo completamos
        if getattr(r, "fecha_arribo", None) is None:
            r.fecha_arribo = now
        if getattr(r, "fecha_recepcion", None) is None:
            r.fecha_recepcion = now

        if getattr(r, "fecha_inicio_descarga", None) is None:
            r.fecha_inicio_descarga = now

        r.estado = RecepcionEstado.EN_DESCARGA

    elif accion == "finalizar_descarga":
        # Solo desde EN_DESCARGA
        if estado_actual != RecepcionEstado.EN_DESCARGA:
            raise InboundDomainError("Solo puedes finalizar descarga desde EN_DESCARGA.")

        if getattr(r, "fecha_inicio_descarga", None) is None:
            r.fecha_inicio_descarga = now
        if getattr(r, "fecha_fin_descarga", None) is None:
            r.fecha_fin_descarga = now

        r.estado = RecepcionEstado.EN_CONTROL_CALIDAD

    elif accion == "cerrar_recepcion":
        # Solo desde EN_CONTROL_CALIDAD
        if estado_actual != RecepcionEstado.EN_CONTROL_CALIDAD:
            raise InboundDomainError("Solo puedes cerrar desde EN_CONTROL_CALIDAD.")
        # Validaciones enterprise (líneas/pallets/docs/etc) viven en otro service (cuando toque)
        r.estado = RecepcionEstado.CERRADO

    else:
        raise InboundDomainError(f"Acción inválida: {accion}")

    # -----------------------------------------
    # ✅ SYNC CITA <- RECEPCIÓN (MISMA TX)
    # -----------------------------------------
    # Nota: NO hace commit dentro (por contrato). Solo muta cita si corresponde.
    sync_cita_desde_recepcion(db, recepcion=r)

    # ✅ Un solo commit garantiza consistencia
    db.add(r)
    db.commit()

    # Refrescar: r y, si existe, la relación cita (si la UI la muestra)
    db.refresh(r)
    return r
