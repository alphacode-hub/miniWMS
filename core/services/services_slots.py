# services_slots.py
from sqlalchemy.orm import Session

from core.models import Slot, Ubicacion, Zona


def get_slots_negocio(db: Session, negocio_id: int):
    """
    Devuelve todos los slots del negocio con información de zona y ubicación.
    Ideal para poblar selects en formularios de movimientos.
    """
    slots = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Zona.negocio_id == negocio_id)
        .order_by(Zona.nombre.asc(), Ubicacion.nombre.asc(), Slot.codigo.asc())
        .all()
    )
    return slots
