# modules/inbound_orbion/services/services_inbound_proveedores.py
"""
Servicios – Proveedores + Plantillas (Inbound ORBION)

✔ Multi-tenant estricto (negocio_id)
✔ Prevención de duplicados útiles (proveedor/plantilla por negocio)
✔ Operaciones consistentes (transacciones y rollback)
✔ Validaciones enterprise (producto pertenece al negocio, plantilla activa opcional)
✔ Compatible con split de modelos (core.models.*)
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import (
    InboundPlantillaProveedor,
    InboundPlantillaProveedorLinea,
    Producto,
    Proveedor,
)
from .services_inbound_core import InboundDomainError


# ============================
#   HELPERS COMUNES
# ============================

def _norm_str(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip()
    return s or None


def _norm_rut(v: Optional[str]) -> Optional[str]:
    s = _norm_str(v)
    return s.upper() if s else None


def _norm_email(v: Optional[str]) -> Optional[str]:
    s = _norm_str(v)
    return s.lower() if s else None


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
    # Si tienes campo activo en Producto, lo respetamos (sin romper si no existe)
    if hasattr(producto, "activo") and getattr(producto, "activo") in (0, False):
        raise InboundDomainError(f"Producto {producto_id} se encuentra inactivo.")
    return producto


def listar_proveedores(
    db: Session,
    negocio_id: int,
    solo_activos: bool = False,
) -> List[Proveedor]:
    q = db.query(Proveedor).filter(Proveedor.negocio_id == negocio_id)
    if solo_activos:
        q = q.filter(Proveedor.activo.is_(True))  # noqa: E712
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
    nombre_norm = _norm_str(nombre)
    if not nombre_norm:
        raise InboundDomainError("El nombre del proveedor es obligatorio.")

    # Evitar duplicados por nombre dentro del negocio (enterprise: consistencia operativa)
    existe = (
        db.query(Proveedor.id)
        .filter(
            Proveedor.negocio_id == negocio_id,
            Proveedor.nombre == nombre_norm,
        )
        .first()
    )
    if existe:
        raise InboundDomainError(
            f"Ya existe un proveedor con el nombre '{nombre_norm}' en este negocio."
        )

    proveedor = Proveedor(
        negocio_id=negocio_id,
        nombre=nombre_norm,
        rut=_norm_rut(rut),
        contacto=_norm_str(contacto),
        telefono=_norm_str(telefono),
        email=_norm_email(email),
        direccion=_norm_str(direccion),
        observaciones=_norm_str(observaciones),
        activo=True,
    )

    try:
        db.add(proveedor)
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError as exc:
        db.rollback()
        # Por si luego agregas unique constraints (rut, email, etc.)
        raise InboundDomainError("No se pudo crear el proveedor (posible duplicado).") from exc


def actualizar_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    **updates: Any,
) -> Proveedor:
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)

    # Normalización controlada
    if "nombre" in updates and updates["nombre"] is not None:
        nombre_norm = _norm_str(str(updates["nombre"]))
        if not nombre_norm:
            raise InboundDomainError("El nombre del proveedor no puede estar vacío.")

        # Evitar colisión por nombre con otro proveedor
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
            raise InboundDomainError(
                f"Ya existe otro proveedor con el nombre '{nombre_norm}'."
            )
        proveedor.nombre = nombre_norm

    # Campos texto opcionales
    if "rut" in updates and updates["rut"] is not None:
        proveedor.rut = _norm_rut(updates["rut"])

    if "contacto" in updates and updates["contacto"] is not None:
        proveedor.contacto = _norm_str(updates["contacto"])

    if "telefono" in updates and updates["telefono"] is not None:
        proveedor.telefono = _norm_str(updates["telefono"])

    if "email" in updates and updates["email"] is not None:
        proveedor.email = _norm_email(updates["email"])

    if "direccion" in updates and updates["direccion"] is not None:
        proveedor.direccion = _norm_str(updates["direccion"])

    if "observaciones" in updates and updates["observaciones"] is not None:
        proveedor.observaciones = _norm_str(updates["observaciones"])

    # Estado activo (si se quiere tocar desde update)
    if "activo" in updates and updates["activo"] is not None:
        proveedor.activo = bool(updates["activo"])

    try:
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar el proveedor (posible duplicado).") from exc


def cambiar_estado_proveedor(
    db: Session,
    negocio_id: int,
    proveedor_id: int,
    activo: bool,
) -> Proveedor:
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)
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
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)

    nombre_norm = _norm_str(nombre)
    if not nombre_norm:
        raise InboundDomainError("El nombre de la plantilla es obligatorio.")

    existente = (
        db.query(InboundPlantillaProveedor.id)
        .filter(
            InboundPlantillaProveedor.negocio_id == negocio_id,
            InboundPlantillaProveedor.proveedor_id == proveedor.id,
            InboundPlantillaProveedor.nombre == nombre_norm,
        )
        .first()
    )
    if existente:
        raise InboundDomainError(
            f"Ya existe una plantilla llamada '{nombre_norm}' para este proveedor."
        )

    plantilla = InboundPlantillaProveedor(
        negocio_id=negocio_id,
        proveedor_id=proveedor.id,
        nombre=nombre_norm,
        descripcion=_norm_str(descripcion),
        activo=True,
    )

    try:
        db.add(plantilla)
        db.commit()
        db.refresh(plantilla)
        return plantilla
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear la plantilla (posible duplicado).") from exc


def actualizar_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    **updates: Any,
) -> InboundPlantillaProveedor:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    if "nombre" in updates and updates["nombre"] is not None:
        nombre_norm = _norm_str(str(updates["nombre"]))
        if not nombre_norm:
            raise InboundDomainError("El nombre de la plantilla no puede estar vacío.")

        # Evitar duplicado por (proveedor_id, nombre) dentro del negocio
        colision = (
            db.query(InboundPlantillaProveedor.id)
            .filter(
                InboundPlantillaProveedor.negocio_id == negocio_id,
                InboundPlantillaProveedor.proveedor_id == plantilla.proveedor_id,
                InboundPlantillaProveedor.nombre == nombre_norm,
                InboundPlantillaProveedor.id != plantilla.id,
            )
            .first()
        )
        if colision:
            raise InboundDomainError(
                f"Ya existe otra plantilla llamada '{nombre_norm}' para este proveedor."
            )

        plantilla.nombre = nombre_norm

    if "descripcion" in updates and updates["descripcion"] is not None:
        plantilla.descripcion = _norm_str(updates["descripcion"])

    if "activo" in updates and updates["activo"] is not None:
        plantilla.activo = bool(updates["activo"])

    try:
        db.commit()
        db.refresh(plantilla)
        return plantilla
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar la plantilla (posible duplicado).") from exc


def cambiar_estado_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    activo: bool,
) -> InboundPlantillaProveedor:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
    plantilla.activo = bool(activo)
    db.commit()
    db.refresh(plantilla)
    return plantilla


def eliminar_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
) -> None:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
    db.delete(plantilla)
    db.commit()


# ============================
#   LÍNEAS DE PLANTILLA
# ============================

def agregar_lineas_a_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    lineas: Iterable[dict[str, Any]],
) -> None:
    """
    Agrega líneas a una plantilla SIN borrar las existentes.

    Reglas enterprise:
    - Producto pertenece al negocio
    - (Opcional) evitar duplicar mismo producto dentro de una plantilla
    """
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    try:
        for data in lineas:
            producto_id_raw = data.get("producto_id")
            if producto_id_raw is None:
                raise InboundDomainError("Cada línea debe incluir 'producto_id'.")

            try:
                producto_id = int(producto_id_raw)
            except (TypeError, ValueError) as exc:
                raise InboundDomainError("producto_id inválido.") from exc

            producto = _validar_producto_de_negocio(db, negocio_id, producto_id)

            # Evitar duplicar producto en la misma plantilla (muy útil en operación)
            dup = (
                db.query(InboundPlantillaProveedorLinea.id)
                .filter(
                    InboundPlantillaProveedorLinea.plantilla_id == plantilla.id,
                    InboundPlantillaProveedorLinea.producto_id == producto.id,
                )
                .first()
            )
            if dup:
                raise InboundDomainError(
                    f"El producto '{producto.nombre}' ya está en esta plantilla."
                )

            linea = InboundPlantillaProveedorLinea(
                plantilla_id=plantilla.id,
                producto_id=producto.id,
                cantidad_sugerida=data.get("cantidad_sugerida"),
                unidad=_norm_str(data.get("unidad")) or getattr(producto, "unidad", None),
                peso_kg_sugerido=data.get("peso_kg_sugerido"),
            )
            db.add(linea)

        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudieron agregar líneas (posible duplicado).") from exc
    except Exception as exc:
        db.rollback()
        raise exc


def reemplazar_lineas_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    plantilla_id: int,
    nuevas_lineas: Iterable[dict[str, Any]],
) -> None:
    """
    Reemplazo total:
    1) Borra todas las líneas actuales
    2) Inserta las nuevas
    Todo en UNA transacción (enterprise).
    """
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    try:
        # Borrar líneas actuales
        db.query(InboundPlantillaProveedorLinea).filter(
            InboundPlantillaProveedorLinea.plantilla_id == plantilla.id
        ).delete()

        # Agregar las nuevas
        for data in nuevas_lineas:
            producto_id_raw = data.get("producto_id")
            if producto_id_raw is None:
                raise InboundDomainError("Cada línea debe incluir 'producto_id'.")

            try:
                producto_id = int(producto_id_raw)
            except (TypeError, ValueError) as exc:
                raise InboundDomainError("producto_id inválido.") from exc

            producto = _validar_producto_de_negocio(db, negocio_id, producto_id)

            linea = InboundPlantillaProveedorLinea(
                plantilla_id=plantilla.id,
                producto_id=producto.id,
                cantidad_sugerida=data.get("cantidad_sugerida"),
                unidad=_norm_str(data.get("unidad")) or getattr(producto, "unidad", None),
                peso_kg_sugerido=data.get("peso_kg_sugerido"),
            )
            db.add(linea)

        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudieron reemplazar líneas (posible duplicado).") from exc
    except Exception as exc:
        db.rollback()
        raise exc
