# modules/inbound_orbion/services/services_inbound_productos_bridge.py
from __future__ import annotations

from sqlalchemy.orm import Session

from core.models import Producto
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError


def crear_producto_rapido_inbound(
    db: Session,
    *,
    negocio_id: int,
    nombre: str,
    unidad: str | None = None,
) -> Producto:
    """
    Bridge Inbound: crea un producto mínimo (activo=1) para destrabar flujos
    como Plantillas/Citas/Recepciones, sin depender del módulo WMS.
    """
    nombre_norm = (nombre or "").strip()
    if not nombre_norm:
        raise InboundDomainError("Debes ingresar un nombre para el producto rápido.")

    unidad_norm = (unidad or "").strip() or "unidad"

    # Evita duplicados "operativos" por nombre (puedes endurecerlo con sku/unique más adelante)
    existe = (
        db.query(Producto)
        .filter(Producto.negocio_id == negocio_id, Producto.nombre == nombre_norm)
        .first()
    )
    if existe:
        # devolvemos el existente para que el flujo continúe
        return existe

    p = Producto(
        negocio_id=negocio_id,
        nombre=nombre_norm,
        unidad=unidad_norm,
        activo=1,
    )
    db.add(p)
    db.flush()  # asigna ID sin commit
    return p
