# modules/inbound_orbion/services/services_inbound_config.py

from datetime import datetime
from sqlalchemy.orm import Session

from core.models import Negocio, InboundConfig, InboundRecepcion, Alerta
from core.plans import get_inbound_plan_config
from modules.inbound_orbion.services.services_inbound import (
    InboundDomainError,
    calcular_metricas_recepcion,
)
from core.logging_config import logger


# ============================
#   CONFIG POR DEFECTO
# ============================

def _default_inbound_config() -> dict:
    """
    Valores razonables por defecto para un negocio nuevo en el módulo inbound.
    Ajusta estos números según tu experiencia real de operación.
    """
    return {
        "sla_espera_obj_min": 30.0,    # 30 min espera máx. (arribo -> inicio descarga)
        "sla_descarga_obj_min": 60.0,  # 60 min descarga máx. (inicio -> fin)
        "sla_total_obj_min": 120.0,    # 120 min total máx. (arribo -> fin)
        "max_incidencias_criticas_por_recepcion": 2,
        "habilitar_alertas_sla": True,
        "habilitar_alertas_incidencias": True,
    }


# ============================
#   GET / CREATE CONFIG
# ============================

def get_or_create_inbound_config(db: Session, negocio_id: int) -> InboundConfig:
    """
    Devuelve la configuración inbound del negocio; si no existe, la crea con
    valores por defecto.
    """
    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )
    if config:
        return config

    defaults = _default_inbound_config()
    config = InboundConfig(
        negocio_id=negocio_id,
        **defaults,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    logger.info(
        "[INBOUND][CONFIG] Creada config por defecto para negocio_id=%s",
        negocio_id,
    )
    return config


# ============================
#   LÓGICA DE PLANES (INBOUND)
# ============================

def _get_plan_limits(negocio: Negocio) -> dict:
    """
    Obtiene la configuración de límites inbound según el plan del negocio.
    Delegamos en core.plans.get_inbound_plan_config(plan_tipo).
    """
    return get_inbound_plan_config(negocio.plan_tipo)


def check_inbound_recepcion_limit(db: Session, negocio: Negocio) -> None:
    """
    Verifica si el negocio aún puede crear recepciones inbound en el mes actual,
    según su plan.
    """
    limits = _get_plan_limits(negocio)
    max_recepciones_mes = limits["max_recepciones_mes"]

    now = datetime.utcnow()
    inicio_mes = datetime(now.year, now.month, 1)
    if now.month == 12:
        fin_mes = datetime(now.year + 1, 1, 1)
    else:
        fin_mes = datetime(now.year, now.month + 1, 1)

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
            negocio.id,
            negocio.plan_tipo,
            total_mes,
            max_recepciones_mes,
        )
        raise InboundDomainError(
            f"Has alcanzado el límite de recepciones inbound ({max_recepciones_mes}) "
            f"para tu plan actual ({negocio.plan_tipo})."
        )


def check_inbound_ml_dataset_permission(negocio: Negocio) -> None:
    """
    Verifica si el plan del negocio permite acceder al dataset avanzado
    para ML/IA.
    """
    limits = _get_plan_limits(negocio)
    if not limits["enable_inbound_ml_dataset"]:
        raise InboundDomainError(
            "Tu plan actual no incluye acceso al dataset avanzado para ML/IA. "
            "Contacta a soporte para actualizar tu plan."
        )


# ============================
#   REGLAS DE SLA / ALERTAS
# ============================

def evaluar_sla_y_generar_alertas(
    db: Session,
    negocio: Negocio,
    recepcion: InboundRecepcion,
) -> None:
    """
    Evalúa las métricas de la recepción contra la configuración del negocio
    y genera alertas (registros en tabla 'alertas') si corresponde.
    """
    config = get_or_create_inbound_config(db, negocio.id)
    metrics = calcular_metricas_recepcion(recepcion)

    alertas_msgs: list[str] = []

    # --- SLA tiempos ---
    if config.habilitar_alertas_sla:
        # Espera
        if config.sla_espera_obj_min and metrics["tiempo_espera_min"] is not None:
            if metrics["tiempo_espera_min"] > config.sla_espera_obj_min:
                alertas_msgs.append(
                    f"Recepción {recepcion.codigo}: tiempo de espera "
                    f"{metrics['tiempo_espera_min']:.1f} min "
                    f"(SLA {config.sla_espera_obj_min:.1f} min)."
                )

        # Descarga
        if config.sla_descarga_obj_min and metrics["tiempo_descarga_min"] is not None:
            if metrics["tiempo_descarga_min"] > config.sla_descarga_obj_min:
                alertas_msgs.append(
                    f"Recepción {recepcion.codigo}: tiempo de descarga "
                    f"{metrics['tiempo_descarga_min']:.1f} min "
                    f"(SLA {config.sla_descarga_obj_min:.1f} min)."
                )

        # Total
        if config.sla_total_obj_min and metrics["tiempo_total_min"] is not None:
            if metrics["tiempo_total_min"] > config.sla_total_obj_min:
                alertas_msgs.append(
                    f"Recepción {recepcion.codigo}: tiempo total "
                    f"{metrics['tiempo_total_min']:.1f} min "
                    f"(SLA {config.sla_total_obj_min:.1f} min)."
                )

    # --- Incidencias críticas ---
    if config.habilitar_alertas_incidencias and config.max_incidencias_criticas_por_recepcion:
        criticas = [i for i in recepcion.incidencias if i.criticidad == "alta"]
        if len(criticas) > config.max_incidencias_criticas_por_recepcion:
            alertas_msgs.append(
                f"Recepción {recepcion.codigo}: {len(criticas)} incidencias de criticidad ALTA."
            )

    # Crear registros en tabla Alertas
    for mensaje in alertas_msgs:
        alerta = Alerta(
            negocio_id=negocio.id,
            tipo="inbound_sla",
            mensaje=mensaje,
            destino=None,  # futuro: whatsapp / email / otro
            estado="pendiente",
            origen="inbound",
        )
        db.add(alerta)

    if alertas_msgs:
        db.commit()
        logger.info(
            "[INBOUND][SLA] negocio_id=%s recepcion_id=%s codigo=%s alertas_generadas=%s",
            negocio.id,
            recepcion.id,
            recepcion.codigo,
            len(alertas_msgs),
        )
