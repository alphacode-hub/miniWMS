# services_audit.py
import json
from sqlalchemy.orm import Session

from models import Auditoria


def registrar_auditoria(
    db: Session,
    user: dict,
    accion: str,
    detalle: dict | str,
) -> None:
    """
    Registra una acción en la tabla de auditoría, asociada al negocio y usuario actual.
    """
    if isinstance(detalle, dict):
        detalle_str = json.dumps(detalle, ensure_ascii=False)
    else:
        detalle_str = str(detalle)

    reg = Auditoria(
        negocio_id=user["negocio_id"],
        usuario=user["email"],
        accion=accion,
        detalle=detalle_str,
    )
    db.add(reg)
    db.commit()
