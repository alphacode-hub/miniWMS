# modules/inbound_orbion/services/services_inbound_proveedores.py

from __future__ import annotations

from typing import Any, Iterable, Optional, List

from sqlalchemy.orm import Session

from core.models import (
    Proveedor,
    InboundPlantillaProveedor,
    InboundPlantillaProveedorLinea,
    Producto,
)
from .services_inbound_core import InboundDomainError


# ============================
#   HELPERS COMUNES
# ============================

def obtener_proveedor_seguro(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
) -> Proveedor:
    """
    Devuelve el proveedor del negocio o lanza InboundDomainError si no existe
    o no pertenece al negocio.
    """
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or proveedor.negocio_id != negocio_id:
        raise InboundDomainError("Proveedor no encontrado para este negocio.")
    return proveedor


def listar_proveedores(
    db: Session,
    negocio_id: int,
    solo_activos: bool = False,
) -> List[Proveedor]:
    """
    Listado de proveedores por negocio, opcionalmente filtrando solo activos.
    """
    q = db.query(Proveedor).filter(Proveedor.negocio_id == negocio_id)
    if solo_activos:
        q = q.filter(Proveedor.activo == True)  # noqa: E712
    return q.order_by(Proveedor.nombre.asc()).all()


# ============================
#   PROVEEDORES
# ============================

def crear_proveedor(
    db: Session,
    negocio_id: int,
    nombre: str,
    rut: Optional[str] = None,
    contacto: Optional[str] = None,
    telefono: Optional[str] = None,
    email: Optional[str] = None,
    direccion: Optional[str] = None,
    observaciones: Optional[str] = None,
) -> Proveedor:
    if not nombre or not nombre.strip():
        raise InboundDomainError("El nombre del proveedor es obligatorio.")

    proveedor = Proveedor(
        negocio_id=negocio_id,
        nombre=nombre.strip(),
        rut=(rut or "").strip() or None,
        contacto=(contacto or "").strip() or None,
        telefono=(telefono or "").strip() or None,
        email=(email or "").strip() or None,
        direccion=(direccion or "").strip() or None,
        observaciones=(observaciones or "").strip() or None,
        activo=True,
    )

    db.add(proveedor)
    db.commit()
    db.refresh(proveedor)
    return proveedor


def actualizar_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    **updates: Any,
) -> Proveedor:
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or proveedor.negocio_id != negocio_id:
        raise InboundDomainError("Proveedor no encontrado para este negocio.")

    campos_texto = {
        "nombre",
        "rut",
        "contacto",
        "telefono",
        "email",
        "direccion",
        "observaciones",
    }

    for field, value in updates.items():
        if not hasattr(proveedor, field):
            continue

        if field in campos_texto and value is not None:
            value = (value or "").strip() or None

        setattr(proveedor, field, value)

    db.commit()
    db.refresh(proveedor)
    return proveedor


def cambiar_estado_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    activo: bool,
) -> Proveedor:
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or proveedor.negocio_id != negocio_id:
        raise InboundDomainError("Proveedor no encontrado para este negocio.")

    proveedor.activo = bool(activo)
    db.commit()
    db.refresh(proveedor)
    return proveedor


# ============================
#   PLANTILLAS DE PROVEEDOR
# ============================

def crear_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    nombre: str,
    descripcion: Optional[str] = None,
) -> InboundPlantillaProveedor:
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or proveedor.negocio_id != negocio_id:
        raise InboundDomainError("Proveedor no encontrado para este negocio.")

    if not nombre or not nombre.strip():
        raise InboundDomainError("El nombre de la plantilla es obligatorio.")

    plantilla = InboundPlantillaProveedor(
        negocio_id=negocio_id,
        proveedor_id=proveedor.id,
        nombre=nombre.strip(),
        descripcion=(descripcion or "").strip() or None,
        activo=True,
    )
    db.add(plantilla)
    db.commit()
    db.refresh(plantilla)
    return plantilla


def actualizar_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    **updates: Any,
) -> InboundPlantillaProveedor:
    """
    Permite actualizar nombre, descripción o estado activo de una plantilla.
    """
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or plantilla.negocio_id != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")

    campos_texto = {"nombre", "descripcion"}

    for field, value in updates.items():
        if not hasattr(plantilla, field):
            continue

        if field in campos_texto and value is not None:
            value = (value or "").strip() or None

        setattr(plantilla, field, value)

    db.commit()
    db.refresh(plantilla)
    return plantilla


def cambiar_estado_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    activo: bool,
) -> InboundPlantillaProveedor:
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or plantilla.negocio_id != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")

    plantilla.activo = bool(activo)
    db.commit()
    db.refresh(plantilla)
    return plantilla


def eliminar_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
) -> None:
    """
    Elimina una plantilla y sus líneas (cascade en el modelo).
    """
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or plantilla.negocio_id != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")

    db.delete(plantilla)
    db.commit()


def agregar_lineas_a_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    lineas: Iterable[dict],
) -> None:
    """
    Agrega líneas a una plantilla existente SIN borrar las existentes.

    lineas: iterable de dicts:
        {
          "producto_id": int,
          "cantidad_sugerida": float | None,
          "unidad": str | None,
          "peso_kg_sugerido": float | None
        }
    """
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or plantilla.negocio_id != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")

    for data in lineas:
        producto_id = int(data.get("producto_id"))
        producto = db.get(Producto, producto_id)
        if not producto or producto.negocio_id != negocio_id:
            raise InboundDomainError(
                f"Producto {producto_id} no pertenece a este negocio."
            )

        linea = InboundPlantillaProveedorLinea(
            plantilla_id=plantilla.id,
            producto_id=producto.id,
            cantidad_sugerida=data.get("cantidad_sugerida"),
            unidad=(data.get("unidad") or "").strip() or None,
            peso_kg_sugerido=data.get("peso_kg_sugerido"),
        )
        db.add(linea)

    db.commit()


def reemplazar_lineas_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    nuevas_lineas: Iterable[dict],
) -> None:
    """
    Uso típico para formulario de edición:
    1) Borra todas las líneas actuales de la plantilla.
    2) Inserta las nuevas líneas entregadas.

    nuevas_lineas: mismo formato que agregar_lineas_a_plantilla_proveedor.
    """
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or plantilla.negocio_id != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")

    # Borrar líneas actuales
    db.query(InboundPlantillaProveedorLinea).filter(
        InboundPlantillaProveedorLinea.plantilla_id == plantilla.id
    ).delete()

    db.commit()

    # Agregar las nuevas
    agregar_lineas_a_plantilla_proveedor(
        db=db,
        negocio_id=negocio_id,
        plantilla_id=plantilla_id,
        lineas=nuevas_lineas,
    )
