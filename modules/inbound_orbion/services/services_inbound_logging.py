# modules/inbound_orbion/services/services_inbound_logging.py
"""
Logging estructurado – Módulo Inbound (ORBION)

✔ Logs JSON (machine-readable)
✔ Child logger por dominio (inbound)
✔ Seguro ante payloads no serializables
✔ Seguro ante colisiones de keys (no rompe flujos)
✔ Preparado para observability (ELK / Loki / Datadog / OpenTelemetry)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.logging_config import logger


# ============================
#   LOGGER DE DOMINIO
# ============================

inbound_logger = logger.getChild("inbound")


# ============================
#   HELPERS
# ============================

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(payload: dict[str, Any]) -> str:
    """
    Serializa a JSON de forma segura.
    - Si un valor no es serializable, se convierte a str().
    - Nunca debe lanzar excepción (logging no debe romper flujos).
    """
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        safe_payload = {k: str(v) for k, v in payload.items()}
        return json.dumps(safe_payload, ensure_ascii=False)


def _merge_extra_safe(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """
    Merge robusto:
    - Evita colisiones de keys (ej: 'tipo', 'event', 'ts', etc.)
    - Si colisiona, renombra como 'extra_<key>' y si aún colisiona agrega sufijo.
    """
    out = dict(base)
    for k, v in (extra or {}).items():
        if k not in out:
            out[k] = v
            continue

        # Colisión: renombramos
        new_key = f"extra_{k}"
        if new_key not in out:
            out[new_key] = v
            continue

        # Si también colisiona, agregamos sufijos
        i = 2
        while True:
            candidate = f"{new_key}_{i}"
            if candidate not in out:
                out[candidate] = v
                break
            i += 1
    return out


def _base_payload(
    *,
    event: str,
    tipo: str,
    negocio_id: int | None,
    recepcion_id: int | None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Estructura base común para todos los logs inbound.
    IMPORTANTE: merge seguro para evitar colisiones.
    """
    base = {
        "ts": _utc_iso(),
        "domain": "inbound",
        "event": f"inbound.{event}",
        "type": tipo,  # event | error | audit | metric
        "negocio_id": negocio_id,
        "recepcion_id": recepcion_id,
    }
    return _merge_extra_safe(base, dict(extra))


# ============================
#   LOGS PÚBLICOS
# ============================

def log_inbound_event(
    event: str,
    *,
    negocio_id: int | None = None,
    recepcion_id: int | None = None,
    **extra: Any,
) -> None:
    """
    Log de evento normal del módulo inbound.
    """
    try:
        payload = _base_payload(
            event=event,
            tipo="event",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            **extra,
        )
        inbound_logger.info(_safe_json(payload))
    except Exception:
        # logging jamás debe romper flujos
        pass


def log_inbound_error(
    event: str,
    *,
    negocio_id: int | None = None,
    recepcion_id: int | None = None,
    error: Exception | str | None = None,
    **extra: Any,
) -> None:
    """
    Log de error del módulo inbound.
    - `error`: excepción o mensaje (opcional)
    """
    try:
        if error is not None:
            if isinstance(error, Exception):
                extra.setdefault("error_type", type(error).__name__)
                extra.setdefault("error_message", str(error))
            else:
                # string u otro
                extra.setdefault("error_type", type(error).__name__)
                extra.setdefault("error_message", str(error))

        payload = _base_payload(
            event=event,
            tipo="error",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            **extra,
        )
        inbound_logger.error(_safe_json(payload))
    except Exception:
        pass


def log_inbound_audit(
    event: str,
    *,
    negocio_id: int,
    recepcion_id: int | None = None,
    usuario: str | None = None,
    **extra: Any,
) -> None:
    """
    Log de auditoría funcional (acciones humanas relevantes).
    """
    try:
        payload = _base_payload(
            event=event,
            tipo="audit",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            usuario=usuario,
            **extra,
        )
        inbound_logger.info(_safe_json(payload))
    except Exception:
        pass


def log_inbound_metric(
    event: str,
    *,
    negocio_id: int | None = None,
    recepcion_id: int | None = None,
    metricas: dict[str, Any],
    **extra: Any,
) -> None:
    """
    Log de métricas calculadas (SLA, tiempos, KPIs).
    """
    try:
        payload = _base_payload(
            event=event,
            tipo="metric",
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            metricas=metricas,
            **extra,
        )
        inbound_logger.info(_safe_json(payload))
    except Exception:
        pass
