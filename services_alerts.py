# services_alerts.py
from sqlalchemy.orm import Session


def evaluar_alertas_stock(
    db: Session,
    user: dict,
    producto_nombre: str,
    origen: str,
) -> None:
    """
    Stub inicial: evaluaciones de alertas de stock.
    Más adelante aquí puedes implementar:
      - stock mínimo
      - stock máximo
      - generación de registros en la tabla Alerta
    """
    return


def evaluar_alertas_vencimiento(
    db: Session,
    user: dict,
    producto_nombre: str,
    origen: str,
) -> None:
    """
    Stub inicial: evaluaciones de alertas de vencimiento.
    Más adelante puedes implementar:
      - próximos a vencer
      - vencidos
      - alertas por producto/negocio
    """
    return
