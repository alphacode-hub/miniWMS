# modules/inbound_orbion/services/services_inbound_core.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from core.models import InboundRecepcion, Producto


# ============================
#   EXCEPCIONES DE DOMINIO
# ============================

class InboundDomainError(Exception):
    """Error de dominio para el módulo Inbound."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ============================
#   CONFIGURACIÓN (WORKFLOWS)
# ============================

@dataclass
class InboundConfig:
    """
    Configuración de reglas de edición / workflow para Inbound (por negocio).

    OJO: esto NO es el modelo SQLAlchemy InboundConfig de SLA; es una
    config de dominio para validar qué se puede editar según estado.
    """
    require_lote: bool = False
    require_fecha_vencimiento: bool = False
    require_temperatura: bool = False
    permitir_editar_en_control_calidad: bool = True
    permitir_editar_cerrado: bool = False

    @classmethod
    def from_negocio(cls, db: Session, negocio_id: int) -> "InboundConfig":
        """
        En el futuro podrías leer esto desde una tabla de configuración
        específica de workflow. De momento devolvemos defaults seguros.
        """
        # TODO: leer de tabla específica de workflow si la creas
        return cls(
            require_lote=False,
            require_fecha_vencimiento=False,
            require_temperatura=True,
            permitir_editar_en_control_calidad=True,
            permitir_editar_cerrado=False,
        )


# ============================
#   HELPERS DE DOMINIO
# ============================

def obtener_recepcion_segura(
    db: Session,
    recepcion_id: int,
    negocio_id: int,
) -> InboundRecepcion:
    recepcion = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.id == recepcion_id,
            InboundRecepcion.negocio_id == negocio_id,
        )
        .first()
    )
    if not recepcion:
        raise InboundDomainError("Recepción inbound no encontrada para este negocio.")
    return recepcion


def validar_recepcion_editable(
    recepcion: InboundRecepcion,
    config: InboundConfig,
) -> None:
    if recepcion.estado == "CERRADO" and not config.permitir_editar_cerrado:
        raise InboundDomainError(
            "La recepción ya fue cerrada y no puede modificarse."
        )

    if recepcion.estado == "EN_CONTROL_CALIDAD" and not config.permitir_editar_en_control_calidad:
        raise InboundDomainError(
            "La recepción está en control de calidad y no admite modificaciones."
        )


def validar_producto_para_negocio(
    db: Session,
    producto_id: int,
    negocio_id: int,
) -> Producto:
    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
        )
        .first()
    )
    if not producto:
        raise InboundDomainError(
            "El producto seleccionado no pertenece al negocio o no existe."
        )
    return producto
