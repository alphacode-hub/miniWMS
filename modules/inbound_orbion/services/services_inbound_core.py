# modules/inbound_orbion/services/services_inbound_core.py
"""
Core de dominio – Inbound (ORBION)

✅ Enterprise rules
- Errores de dominio tipados (no HTTP aquí)
- Estados estrictos (enum del core)
- Helpers seguros: ownership multi-tenant + reglas de edición
- Sin "config" duplicada: usa el modelo InboundConfig del core
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from core.models import Producto
from core.models.inbound import InboundConfig, InboundRecepcion, RecepcionEstado


# =========================================================
# EXCEPCIONES DE DOMINIO
# =========================================================

class InboundDomainError(Exception):
    """Error de dominio para el módulo Inbound."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


# =========================================================
# ESTADOS (canon)
# =========================================================

ESTADO_PRE_REGISTRADO: Final[str] = RecepcionEstado.PRE_REGISTRADO.value
ESTADO_EN_ESPERA: Final[str] = RecepcionEstado.EN_ESPERA.value
ESTADO_EN_DESCARGA: Final[str] = RecepcionEstado.EN_DESCARGA.value
ESTADO_EN_CONTROL_CALIDAD: Final[str] = RecepcionEstado.EN_CONTROL_CALIDAD.value
ESTADO_CERRADO: Final[str] = RecepcionEstado.CERRADO.value

ESTADOS_RECEPCION_VALIDOS = {e.value for e in RecepcionEstado}
ESTADOS_RECEPCION_VALIDOS_UP = {e.value.upper() for e in RecepcionEstado}

def normalizar_estado_recepcion(estado: str | None) -> str:
    raw = (estado or "").strip()
    if not raw:
        return RecepcionEstado.PRE_REGISTRADO.value
    up = raw.upper()
    if up not in ESTADOS_RECEPCION_VALIDOS_UP:
        raise InboundDomainError(f"Estado de recepción inválido: '{estado}'.")
    # devolver CANON EXACTO (el value original)
    for e in RecepcionEstado:
        if e.value.upper() == up:
            return e.value
    return RecepcionEstado.PRE_REGISTRADO.value  # fallback nunca debería ocurrir



# =========================================================
# HELPERS DE DOMINIO (multi-tenant + workflow)
# =========================================================

def obtener_recepcion_segura(
    db: Session,
    recepcion_id: int,
    negocio_id: int,
) -> InboundRecepcion:
    """
    Devuelve la recepción si pertenece al negocio; si no, lanza InboundDomainError.
    """
    recepcion = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.id == recepcion_id,
            InboundRecepcion.negocio_id == negocio_id,
        )
        .first()
    )
    if recepcion is None:
        raise InboundDomainError("Recepción inbound no encontrada para este negocio.")
    return recepcion


def obtener_config_inbound(
    db: Session,
    negocio_id: int,
) -> InboundConfig:
    """
    Obtiene la configuración inbound del negocio.

    - Si no existe, crea una configuración por defecto (idempotente).
    - Evita que cada servicio tenga que “inventar defaults”.
    """
    cfg = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )
    if cfg is not None:
        return cfg

    cfg = InboundConfig(
        negocio_id=negocio_id,
        # Defaults enterprise razonables:
        require_lote=False,
        require_fecha_vencimiento=False,
        require_temperatura=True,
        permitir_editar_en_control_calidad=True,
        permitir_editar_cerrado=False,
    )
    db.add(cfg)
    db.flush()
    return cfg


def validar_recepcion_editable(
    recepcion: InboundRecepcion,
    config: InboundConfig,
) -> None:
    """
    Valida si una recepción puede ser editada según su estado y reglas del negocio.
    """
    estado = normalizar_estado_recepcion(getattr(recepcion, "estado", None))

    if estado == ESTADO_CERRADO and not config.permitir_editar_cerrado:
        raise InboundDomainError("La recepción ya fue cerrada y no puede modificarse.")

    if estado == ESTADO_EN_CONTROL_CALIDAD and not config.permitir_editar_en_control_calidad:
        raise InboundDomainError("La recepción está en control de calidad y no admite modificaciones.")


def obtener_recepcion_editable(
    db: Session,
    recepcion_id: int,
    negocio_id: int,
) -> InboundRecepcion:
    """
    Helper enterprise: obtiene recepción segura + valida si es editable según workflow.
    Úsalo en servicios que mutan estado/datos (líneas, pallets, incidencias, etc.).
    """
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    cfg = obtener_config_inbound(db, negocio_id)
    validar_recepcion_editable(recepcion, cfg)
    return recepcion


def validar_producto_para_negocio(
    db: Session,
    producto_id: int,
    negocio_id: int,
) -> Producto:
    """
    Valida que el producto exista, pertenezca al negocio y esté activo.
    """
    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .first()
    )
    if producto is None:
        raise InboundDomainError(
            "El producto seleccionado no pertenece al negocio, no existe o se encuentra inactivo."
        )
    return producto
