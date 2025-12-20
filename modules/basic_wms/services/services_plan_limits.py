# modules/basic_wms/services/services_plan_limits.py

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.models import (
    Negocio,
    Usuario,
    Producto,
    Zona,
    Ubicacion,
    Slot,
)


def check_plan_limit(db: Session, negocio_id: int, recurso: str) -> None:
    """
    Verifica si el negocio puede crear más registros para el recurso indicado
    según su plan. Si se supera el límite, lanza HTTPException 400.

    recurso esperado: "usuarios", "productos", "zonas", "ubicaciones", "slots"
    """
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado.")

    plan_tipo = negocio.plan_tipo or "demo"
    plan_cfg = get_core_plan_config(plan_tipo)  # 👈 usamos helper global

    max_key = f"max_{recurso}"
    max_val = plan_cfg.get(max_key)

    # Si el plan no define límite para ese recurso, no hacemos nada
    if max_val is None:
        return

    # Contar registros actuales según el recurso
    if recurso == "usuarios":
        count = db.query(Usuario).filter(Usuario.negocio_id == negocio.id).count()

    elif recurso == "productos":
        count = db.query(Producto).filter(Producto.negocio_id == negocio.id).count()

    elif recurso == "zonas":
        count = db.query(Zona).filter(Zona.negocio_id == negocio.id).count()

    elif recurso == "ubicaciones":
        count = (
            db.query(Ubicacion)
            .join(Zona, Ubicacion.zona_id == Zona.id)
            .filter(Zona.negocio_id == negocio.id)
            .count()
        )

    elif recurso == "slots":
        count = (
            db.query(Slot)
            .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
            .join(Zona, Ubicacion.zona_id == Zona.id)
            .filter(Zona.negocio_id == negocio.id)
            .count()
        )

    else:
        # recurso desconocido → no aplicamos límite
        return

    if count >= max_val:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Has alcanzado el límite de {recurso} "
                f"({count}/{max_val}) para el plan '{plan_tipo}'."
            ),
        )
