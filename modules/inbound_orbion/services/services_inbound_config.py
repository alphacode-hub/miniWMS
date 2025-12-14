# modules/inbound_orbion/services/services_inbound_config.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.plans import get_inbound_plan_config
from core.models import Negocio, Alerta
from core.models.inbound import InboundConfig, InboundRecepcion

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from modules.inbound_orbion.services.services_inbound_analytics import calcular_metricas_recepcion


# ============================
#   TIME (UTC)
# ============================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================
#   DEFAULTS / VALIDATION
# ============================

@dataclass(frozen=True)
class InboundConfigDefaults:
    sla_espera_obj_min: float = 30.0
    sla_descarga_obj_min: float = 60.0
    sla_total_obj_min: float = 120.0
    max_incidencias_criticas_por_recepcion: int = 2
    habilitar_alertas_sla: bool = True
    habilitar_alertas_incidencias: bool = True


DEFAULTS: Final[InboundConfigDefaults] = InboundConfigDefaults()


def _default_inbound_config_dict() -> dict[str, Any]:
    """
    Valores por defecto para un negocio nuevo en Inbound.
    """
    return {
        "sla_espera_obj_min": float(DEFAULTS.sla_espera_obj_min),
        "sla_descarga_obj_min": float(DEFAULTS.sla_descarga_obj_min),
        "sla_total_obj_min": float(DEFAULTS.sla_total_obj_min),
        "max_incidencias_criticas_por_recepcion": int(DEFAULTS.max_incidencias_criticas_por_recepcion),
        "habilitar_alertas_sla": bool(DEFAULTS.habilitar_alertas_sla),
        "habilitar_alertas_incidencias": bool(DEFAULTS.habilitar_alertas_incidencias),
    }


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


# ============================
#   GET / CREATE CONFIG
# ============================

def get_or_create_inbound_config(db: Session, negocio_id: int) -> InboundConfig:
    """
    Devuelve la configuración inbound del negocio; si no existe, la crea con defaults.
    """
    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )
    if config:
        return config

    config = InboundConfig(
        negocio_id=negocio_id,
        **_default_inbound_config_dict(),
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    logger.info("[INBOUND][CONFIG] Creada config por defecto negocio_id=%s", negocio_id)
    return config


# ============================
#   LÓGICA DE PLANES (INBOUND)
# ============================

def _get_plan_limits(plan_tipo: str | None) -> dict[str, Any]:
    """
    Obtiene límites inbound según plan.
    """
    return get_inbound_plan_config((plan_tipo or "demo").strip().lower())


def _month_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    """
    Retorna (inicio_mes, fin_mes) en UTC, donde fin_mes es exclusivo.
    """
    now = now.astimezone(timezone.utc)
    inicio_mes = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        fin_mes = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        fin_mes = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return inicio_mes, fin_mes


def check_inbound_recepcion_limit(db: Session, negocio: Negocio) -> None:
    """
    Verifica límite de creación de recepciones inbound en el mes actual según plan.

    - Si el plan no define 'max_recepciones_mes', no restringe.
    - Si supera, lanza InboundDomainError.
    """
    limits = _get_plan_limits(negocio.plan_tipo)
    max_recepciones_mes = _coerce_int(limits.get("max_recepciones_mes"))

    if max_recepciones_mes is None:
        return

    inicio_mes, fin_mes = _month_bounds_utc(utcnow())

    total_mes = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.negocio_id == negocio.id,
            InboundRecepcion.creado_en >= inicio_mes,
            InboundRecepcion.creado_en < fin_mes,
        )
        .count()
    )

    if total_mes >= max_recepciones_mes:
        logger.warning(
            "[INBOUND][PLAN_LIMIT] negocio_id=%s plan=%s total_mes=%s max=%s",
            negocio.id, negocio.plan_tipo, total_mes, max_recepciones_mes,
        )
        raise InboundDomainError(
            f"Has alcanzado el límite de recepciones inbound ({max_recepciones_mes}) "
            f"para tu plan actual ({negocio.plan_tipo})."
        )


def check_inbound_ml_dataset_permission(negocio: Negocio) -> None:
    """
    Verifica si el plan permite acceder a dataset ML/IA.
    """
    limits = _get_plan_limits(negocio.plan_tipo)
    allow_ml_dataset = bool(limits.get("enable_inbound_ml_dataset", False))

    if not allow_ml_dataset:
        raise InboundDomainError(
            "Tu plan actual no incluye acceso al dataset avanzado para ML/IA. "
            "Contacta a soporte para actualizar tu plan."
        )


# ============================
#   SLA / ALERTAS
# ============================

def _is_already_pending_alert(
    db: Session,
    negocio_id: int,
    tipo: str,
    mensaje: str,
) -> bool:
    """
    Evita spam: si existe una alerta pendiente idéntica, no la vuelve a crear.
    """
    exists = (
        db.query(Alerta.id)
        .filter(
            Alerta.negocio_id == negocio_id,
            Alerta.tipo == tipo,
            Alerta.estado == "pendiente",
            Alerta.mensaje == mensaje,
        )
        .first()
    )
    return bool(exists)


def evaluar_sla_y_generar_alertas(
    db: Session,
    negocio: Negocio,
    recepcion: InboundRecepcion,
) -> int:
    """
    Evalúa SLA e incidencias y genera alertas globales (tabla 'alertas').

    Retorna: cantidad de alertas creadas.
    """
    config = get_or_create_inbound_config(db, negocio.id)
    metrics = calcular_metricas_recepcion(recepcion)

    created = 0
    mensajes: list[str] = []

    # --- SLA tiempos ---
    if bool(getattr(config, "habilitar_alertas_sla", False)):
        sla_espera = _coerce_float(getattr(config, "sla_espera_obj_min", None))
        sla_descarga = _coerce_float(getattr(config, "sla_descarga_obj_min", None))
        sla_total = _coerce_float(getattr(config, "sla_total_obj_min", None))

        if sla_espera and metrics.get("tiempo_espera_min") is not None:
            if float(metrics["tiempo_espera_min"]) > sla_espera:
                mensajes.append(
                    f"Recepción {recepcion.codigo}: tiempo de espera "
                    f"{float(metrics['tiempo_espera_min']):.1f} min (SLA {sla_espera:.1f} min)."
                )

        if sla_descarga and metrics.get("tiempo_descarga_min") is not None:
            if float(metrics["tiempo_descarga_min"]) > sla_descarga:
                mensajes.append(
                    f"Recepción {recepcion.codigo}: tiempo de descarga "
                    f"{float(metrics['tiempo_descarga_min']):.1f} min (SLA {sla_descarga:.1f} min)."
                )

        if sla_total and metrics.get("tiempo_total_min") is not None:
            if float(metrics["tiempo_total_min"]) > sla_total:
                mensajes.append(
                    f"Recepción {recepcion.codigo}: tiempo total "
                    f"{float(metrics['tiempo_total_min']):.1f} min (SLA {sla_total:.1f} min)."
                )

    # --- Incidencias críticas ---
    if bool(getattr(config, "habilitar_alertas_incidencias", False)):
        max_crit = _coerce_int(getattr(config, "max_incidencias_criticas_por_recepcion", None))
        if max_crit and hasattr(recepcion, "incidencias") and recepcion.incidencias:
            criticas = [i for i in recepcion.incidencias if (getattr(i, "criticidad", "") or "").lower() == "alta"]
            if len(criticas) > max_crit:
                mensajes.append(
                    f"Recepción {recepcion.codigo}: {len(criticas)} incidencias de criticidad ALTA."
                )

    # Crear alertas (dedupe por mensaje pendiente)
    for msg in mensajes:
        tipo = "inbound_sla"
        if _is_already_pending_alert(db, negocio.id, tipo, msg):
            continue

        alerta = Alerta(
            negocio_id=negocio.id,
            tipo=tipo,
            mensaje=msg,
            destino=None,      # futuro: whatsapp / email / otro canal
            estado="pendiente",
            origen="inbound",
        )
        db.add(alerta)
        created += 1

    if created:
        db.commit()
        logger.info(
            "[INBOUND][SLA] negocio_id=%s recepcion_id=%s codigo=%s alertas_creadas=%s",
            negocio.id, recepcion.id, recepcion.codigo, created,
        )

    return created
