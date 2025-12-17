"""
Servicios – Proveedores + Plantillas (Inbound ORBION)

✔ Multi-tenant estricto (negocio_id)
✔ Prevención de duplicados operativos
✔ Rollback seguro
✔ Normalización de datos
✔ Plantillas proveedor + líneas (base para citas y prealertas)
✔ Compatible con baseline Inbound actual
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import (
    Proveedor,
    Producto,
    InboundPlantillaProveedor,
    InboundPlantillaProveedorLinea,
)

from .services_inbound_core import InboundDomainError


# =========================================================
# HELPERS INTERNOS
# =========================================================

def _norm_str(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip()
    return s or None


def _norm_rut(v: Optional[str]) -> Optional[str]:
    s = _norm_str(v)
    return s.upper() if s else None


def _norm_email(v: Optional[str]) -> Optional[str]:
    s = _norm_str(v)
    return s.lower() if s else None


# =========================================================
# VALIDACIONES SEGURAS
# =========================================================

def obtener_proveedor_seguro(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
) -> Proveedor:
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or proveedor.negocio_id != negocio_id:
        raise InboundDomainError("Proveedor no encontrado para este negocio.")
    return proveedor


def obtener_plantilla_segura(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
) -> InboundPlantillaProveedor:
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or plantilla.negocio_id != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")
    return plantilla


def _validar_producto_de_negocio(
    db: Session,
    negocio_id: int,
    producto_id: int,
) -> Producto:
    producto = db.get(Producto, producto_id)
    if not producto or producto.negocio_id != negocio_id:
        raise InboundDomainError(f"Producto {producto_id} no pertenece a este negocio.")

    if hasattr(producto, "activo") and getattr(producto, "activo") in (0, False):
        raise InboundDomainError(f"Producto {producto.nombre} se encuentra inactivo.")

    return producto


# =========================================================
# PROVEEDORES
# =========================================================

def listar_proveedores(
    db: Session,
    negocio_id: int,
    solo_activos: bool = False,
) -> List[Proveedor]:
    q = db.query(Proveedor).filter(Proveedor.negocio_id == negocio_id)
    if solo_activos:
        q = q.filter(Proveedor.activo == 1)
    return q.order_by(Proveedor.nombre.asc()).all()


def crear_proveedor(
    db: Session,
    negocio_id: int,
    nombre: str,
    rut: Optional[str] = None,
    email: Optional[str] = None,
    telefono: Optional[str] = None,
) -> Proveedor:
    nombre_norm = _norm_str(nombre)
    if not nombre_norm:
        raise InboundDomainError("El nombre del proveedor es obligatorio.")

    existe = (
        db.query(Proveedor.id)
        .filter(
            Proveedor.negocio_id == negocio_id,
            Proveedor.nombre == nombre_norm,
        )
        .first()
    )
    if existe:
        raise InboundDomainError(f"Ya existe un proveedor llamado '{nombre_norm}'.")

    proveedor = Proveedor(
        negocio_id=negocio_id,
        nombre=nombre_norm,
        rut=_norm_rut(rut),
        email=_norm_email(email),
        telefono=_norm_str(telefono),
        activo=1,
    )

    try:
        db.add(proveedor)
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo crear el proveedor (posible duplicado).")


def actualizar_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    **updates: Any,
) -> Proveedor:
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)

    if "nombre" in updates and updates["nombre"] is not None:
        nombre_norm = _norm_str(updates["nombre"])
        if not nombre_norm:
            raise InboundDomainError("El nombre no puede estar vacío.")

        colision = (
            db.query(Proveedor.id)
            .filter(
                Proveedor.negocio_id == negocio_id,
                Proveedor.nombre == nombre_norm,
                Proveedor.id != proveedor.id,
            )
            .first()
        )
        if colision:
            raise InboundDomainError(f"Ya existe otro proveedor llamado '{nombre_norm}'.")
        proveedor.nombre = nombre_norm

    if "rut" in updates:
        proveedor.rut = _norm_rut(updates["rut"])

    if "email" in updates:
        proveedor.email = _norm_email(updates["email"])

    if "telefono" in updates:
        proveedor.telefono = _norm_str(updates["telefono"])

    if "activo" in updates and updates["activo"] is not None:
        proveedor.activo = 1 if updates["activo"] else 0

    try:
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar el proveedor.")


def cambiar_estado_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    activo: bool,
) -> Proveedor:
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)
    proveedor.activo = 1 if activo else 0
    db.commit()
    db.refresh(proveedor)
    return proveedor


# =========================================================
# PLANTILLAS DE PROVEEDOR
# =========================================================

def crear_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    nombre: str,
    descripcion: Optional[str] = None,
) -> InboundPlantillaProveedor:
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)

    nombre_norm = _norm_str(nombre)
    if not nombre_norm:
        raise InboundDomainError("El nombre de la plantilla es obligatorio.")

    existe = (
        db.query(InboundPlantillaProveedor.id)
        .filter(
            InboundPlantillaProveedor.negocio_id == negocio_id,
            InboundPlantillaProveedor.proveedor_id == proveedor.id,
            InboundPlantillaProveedor.nombre == nombre_norm,
        )
        .first()
    )
    if existe:
        raise InboundDomainError("Ya existe una plantilla con ese nombre.")

    plantilla = InboundPlantillaProveedor(
        negocio_id=negocio_id,
        proveedor_id=proveedor.id,
        nombre=nombre_norm,
        descripcion=_norm_str(descripcion),
        activo=1,
    )

    try:
        db.add(plantilla)
        db.commit()
        db.refresh(plantilla)
        return plantilla
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo crear la plantilla.")


def actualizar_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    **updates: Any,
) -> InboundPlantillaProveedor:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    if "nombre" in updates and updates["nombre"] is not None:
        nombre_norm = _norm_str(updates["nombre"])
        if not nombre_norm:
            raise InboundDomainError("El nombre no puede estar vacío.")
        plantilla.nombre = nombre_norm

    if "descripcion" in updates:
        plantilla.descripcion = _norm_str(updates["descripcion"])

    if "activo" in updates and updates["activo"] is not None:
        plantilla.activo = 1 if updates["activo"] else 0

    try:
        db.commit()
        db.refresh(plantilla)
        return plantilla
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar la plantilla.")


def eliminar_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
) -> None:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
    db.delete(plantilla)
    db.commit()


# =========================================================
# LÍNEAS DE PLANTILLA
# =========================================================

def agregar_lineas_a_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    lineas: Iterable[dict[str, Any]],
) -> None:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    try:
        for data in lineas:
            producto_id = int(data.get("producto_id"))
            producto = _validar_producto_de_negocio(db, negocio_id, producto_id)

            existe = (
                db.query(InboundPlantillaProveedorLinea.id)
                .filter(
                    InboundPlantillaProveedorLinea.plantilla_id == plantilla.id,
                    InboundPlantillaProveedorLinea.producto_id == producto.id,
                )
                .first()
            )
            if existe:
                raise InboundDomainError(
                    f"El producto '{producto.nombre}' ya existe en la plantilla."
                )

            linea = InboundPlantillaProveedorLinea(
                plantilla_id=plantilla.id,
                producto_id=producto.id,
                cantidad_sugerida=data.get("cantidad_sugerida"),
                peso_kg_sugerido=data.get("peso_kg_sugerido"),
                unidad=_norm_str(data.get("unidad")) or getattr(producto, "unidad", None),
            )
            db.add(linea)

        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def reemplazar_lineas_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    nuevas_lineas: Iterable[dict[str, Any]],
) -> None:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    try:
        db.query(InboundPlantillaProveedorLinea).filter(
            InboundPlantillaProveedorLinea.plantilla_id == plantilla.id
        ).delete()

        for data in nuevas_lineas:
            producto_id = int(data.get("producto_id"))
            producto = _validar_producto_de_negocio(db, negocio_id, producto_id)

            linea = InboundPlantillaProveedorLinea(
                plantilla_id=plantilla.id,
                producto_id=producto.id,
                cantidad_sugerida=data.get("cantidad_sugerida"),
                peso_kg_sugerido=data.get("peso_kg_sugerido"),
                unidad=_norm_str(data.get("unidad")) or getattr(producto, "unidad", None),
            )
            db.add(linea)

        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
