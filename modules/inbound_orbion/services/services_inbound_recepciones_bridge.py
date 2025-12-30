# modules/inbound_orbion/services/services_inbound_recepciones_bridge.py
from __future__ import annotations

from sqlalchemy.orm import Session

from core.models.inbound.recepciones import InboundRecepcion
from modules.inbound_orbion.services.services_inbound_recepcion_estados import aplicar_accion_estado


def cerrar_recepcion_bridge(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
) -> InboundRecepcion:
    """
    ÚNICA verdad para cerrar una recepción.
    Se usa desde rutas, jobs, o cualquier otro service.
    """
    return aplicar_accion_estado(
        db,
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        accion="cerrar_recepcion",
    )
