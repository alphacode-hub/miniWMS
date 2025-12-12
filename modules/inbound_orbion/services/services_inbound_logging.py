# modules/inbound_orbion/services/services_inbound_logging.py

import json

from core.logging_config import logger

# Logger hijo específico para el módulo inbound
inbound_logger = logger.getChild("inbound")


def log_inbound_event(
    event: str,
    negocio_id: int | None = None,
    recepcion_id: int | None = None,
    **extra,
) -> None:
    """
    Log estructurado para eventos normales del módulo inbound.
    Se loguea en formato JSON para que sea fácil de parsear por
    cualquier herramienta de observabilidad.
    """
    payload = {
        "event": f"inbound.{event}",
        "negocio_id": negocio_id,
        "recepcion_id": recepcion_id,
        **extra,
    }
    inbound_logger.info(json.dumps(payload, ensure_ascii=False))


def log_inbound_error(
    event: str,
    negocio_id: int | None = None,
    recepcion_id: int | None = None,
    **extra,
) -> None:
    """
    Log estructurado para errores del módulo inbound.
    """
    payload = {
        "event": f"inbound.{event}",
        "negocio_id": negocio_id,
        "recepcion_id": recepcion_id,
        **extra,
    }
    inbound_logger.error(json.dumps(payload, ensure_ascii=False))
