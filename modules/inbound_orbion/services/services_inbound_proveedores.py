# modules/inbound_orbion/services/services_inbound_proveedores.py
"""
Servicios – Proveedores + Plantillas proveedor (Inbound ORBION, baseline aligned)

✔ Multi-tenant estricto (negocio_id)
✔ Normalización (nombre/rut/email)
✔ Prevención duplicados operativos
✔ Rollback seguro
✔ Límite de proveedores por entitlements: ent["limits"]["inbound"]["proveedores"]
✔ Plantillas proveedor + líneas (base para citas/prealertas)
✔ Plantilla/Líneas alineadas a core/models/inbound/plantillas.py (SIN descripcion en plantilla,
  SIN cantidad_sugerida/peso_kg_sugerido en líneas)
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from core.logging_config import logger
from core.models import Proveedor, Producto
from core.models.inbound import InboundPlantillaProveedor, InboundPlantillaProveedorLinea
from core.models.time import utcnow
from core.services.services_entitlements import resolve_entitlements

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


def _get_inbound_limits(ent: dict) -> dict[str, Any]:
    limits_all = ent.get("limits")
    if not isinstance(limits_all, dict):
        return {}
    inbound = limits_all.get("inbound")
    return inbound if isinstance(inbound, dict) else {}


def _coerce_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    try:
        return int(float(str(v).strip().replace(",", ".")))
    except Exception:
        return None


def _validar_producto_de_negocio(db: Session, negocio_id: int, producto_id: int) -> Producto:
    producto = db.get(Producto, producto_id)
    if not producto or getattr(producto, "negocio_id", None) != negocio_id:
        raise InboundDomainError(f"Producto {producto_id} no pertenece a este negocio.")

    if hasattr(producto, "activo") and getattr(producto, "activo") in (0, False):
        raise InboundDomainError(
            f"Producto '{getattr(producto, 'nombre', 'Producto')}' se encuentra inactivo."
        )

    return producto


# =========================================================
# VALIDACIONES SEGURAS
# =========================================================

def obtener_proveedor_seguro(db: Session, negocio_id: int, proveedor_id: int) -> Proveedor:
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or getattr(proveedor, "negocio_id", None) != negocio_id:
        raise InboundDomainError("Proveedor no encontrado para este negocio.")
    return proveedor


def obtener_plantilla_segura(db: Session, negocio_id: int, plantilla_id: int) -> InboundPlantillaProveedor:
    plantilla = db.get(InboundPlantillaProveedor, plantilla_id)
    if not plantilla or getattr(plantilla, "negocio_id", None) != negocio_id:
        raise InboundDomainError("Plantilla de proveedor no encontrada para este negocio.")
    return plantilla


def obtener_linea_plantilla_segura(db: Session, negocio_id: int, linea_id: int) -> InboundPlantillaProveedorLinea:
    linea = db.get(InboundPlantillaProveedorLinea, linea_id)
    if not linea:
        raise InboundDomainError("Línea de plantilla no encontrada.")

    plantilla = db.get(InboundPlantillaProveedor, getattr(linea, "plantilla_id", None))
    if not plantilla or getattr(plantilla, "negocio_id", None) != negocio_id:
        raise InboundDomainError("Línea no pertenece a este negocio.")

    return linea


# =========================================================
# PROVEEDORES
# =========================================================

def listar_proveedores(db: Session, negocio_id: int, *, solo_activos: bool = False) -> List[Proveedor]:
    q = db.query(Proveedor).filter(Proveedor.negocio_id == negocio_id)
    if solo_activos:
        q = q.filter(Proveedor.activo == 1)
    return q.order_by(Proveedor.nombre.asc()).all()


def contar_proveedores(db: Session, negocio_id: int, *, solo_activos: bool = False) -> int:
    q = db.query(Proveedor).filter(Proveedor.negocio_id == negocio_id)
    if solo_activos:
        q = q.filter(Proveedor.activo == 1)
    return int(q.count())


def crear_proveedor(
    db: Session,
    *,
    negocio_id: int,
    nombre: str,
    rut: Optional[str] = None,
    email: Optional[str] = None,
    telefono: Optional[str] = None,
    contacto: Optional[str] = None,
    direccion: Optional[str] = None,
    observaciones: Optional[str] = None,
    # ✅ si el caller ya tiene el Negocio, pásalo para enforcement entitlements sin query extra
    negocio: Any | None = None,
) -> Proveedor:
    nombre_norm = _norm_str(nombre)
    if not nombre_norm:
        raise InboundDomainError("El nombre del proveedor es obligatorio.")

    # ✅ enforcement límite por entitlements (si el caller pasa Negocio)
    if negocio is not None:
        ent = resolve_entitlements(negocio)
        inbound_limits = _get_inbound_limits(ent)
        max_proveedores = _coerce_int(inbound_limits.get("proveedores"))
        if max_proveedores is not None:
            total = contar_proveedores(db, negocio_id, solo_activos=False)
            if total >= max_proveedores:
                raise InboundDomainError(
                    f"Has alcanzado el límite de proveedores ({max_proveedores}) para este negocio."
                )

    # anti-duplicado operativo por nombre (exact match normalizado)
    existe = (
        db.query(Proveedor.id)
        .filter(Proveedor.negocio_id == negocio_id, Proveedor.nombre == nombre_norm)
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
        contacto=_norm_str(contacto),
        direccion=_norm_str(direccion),
        observaciones=_norm_str(observaciones),
        activo=1,
    )

    # timestamps (baseline safe)
    if hasattr(proveedor, "created_at"):
        proveedor.created_at = utcnow()
    if hasattr(proveedor, "updated_at"):
        proveedor.updated_at = utcnow()

    try:
        db.add(proveedor)
        db.commit()
        db.refresh(proveedor)
        logger.info("[INBOUND][PROV] creado negocio_id=%s proveedor_id=%s", negocio_id, proveedor.id)
        return proveedor
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo crear el proveedor (posible duplicado).")


def actualizar_proveedor(
    db: Session,
    *,
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

    if "contacto" in updates:
        proveedor.contacto = _norm_str(updates["contacto"])

    if "direccion" in updates:
        proveedor.direccion = _norm_str(updates["direccion"])

    if "observaciones" in updates:
        proveedor.observaciones = _norm_str(updates["observaciones"])

    if "activo" in updates and updates["activo"] is not None:
        proveedor.activo = 1 if bool(updates["activo"]) else 0

    if hasattr(proveedor, "updated_at"):
        proveedor.updated_at = utcnow()

    try:
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar el proveedor.")


def cambiar_estado_proveedor(db: Session, *, negocio_id: int, proveedor_id: int, activo: bool) -> Proveedor:
    proveedor = obtener_proveedor_seguro(db, negocio_id, proveedor_id)
    proveedor.activo = 1 if activo else 0
    if hasattr(proveedor, "updated_at"):
        proveedor.updated_at = utcnow()
    db.commit()
    db.refresh(proveedor)
    return proveedor


# =========================================================
# PLANTILLAS PROVEEDOR (ALINEADO A MODELO)
# =========================================================

def listar_plantillas_proveedor(
    db: Session,
    negocio_id: int,
    *,
    proveedor_id: int | None = None,
    solo_activas: bool = False,
) -> List[InboundPlantillaProveedor]:
    q = db.query(InboundPlantillaProveedor).filter(InboundPlantillaProveedor.negocio_id == negocio_id)
    if proveedor_id is not None:
        q = q.filter(InboundPlantillaProveedor.proveedor_id == proveedor_id)
    if solo_activas:
        q = q.filter(InboundPlantillaProveedor.activo == 1)
    return q.order_by(InboundPlantillaProveedor.nombre.asc()).all()


def crear_plantilla_proveedor(
    db: Session,
    *,
    negocio_id: int,
    proveedor_id: int,
    nombre: str,
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
        raise InboundDomainError("Ya existe una plantilla con ese nombre para este proveedor.")

    plantilla = InboundPlantillaProveedor(
        negocio_id=negocio_id,
        proveedor_id=proveedor.id,
        nombre=nombre_norm,
        activo=1,
    )

    if hasattr(plantilla, "created_at"):
        plantilla.created_at = utcnow()

    try:
        db.add(plantilla)
        db.commit()
        db.refresh(plantilla)
        return plantilla
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo crear la plantilla (posible duplicado).")


def actualizar_plantilla_proveedor(
    db: Session,
    *,
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

    if "activo" in updates and updates["activo"] is not None:
        plantilla.activo = 1 if bool(updates["activo"]) else 0

    try:
        db.commit()
        db.refresh(plantilla)
        return plantilla
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar la plantilla.")


def cambiar_estado_plantilla_proveedor(
    db: Session,
    *,
    negocio_id: int,
    plantilla_id: int,
    activo: bool,
) -> InboundPlantillaProveedor:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
    plantilla.activo = 1 if activo else 0
    db.commit()
    db.refresh(plantilla)
    return plantilla


def eliminar_plantilla_proveedor(db: Session, *, negocio_id: int, plantilla_id: int) -> None:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
    db.delete(plantilla)
    db.commit()


# =========================================================
# LÍNEAS DE PLANTILLA (ALINEADO A MODELO)
# =========================================================

def listar_lineas_plantilla_proveedor(
    db: Session,
    negocio_id: int,
    *,
    plantilla_id: int,
    solo_activas: bool = False,
) -> List[InboundPlantillaProveedorLinea]:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)

    q = (
        db.query(InboundPlantillaProveedorLinea)
        .options(joinedload(InboundPlantillaProveedorLinea.producto))
        .filter(InboundPlantillaProveedorLinea.plantilla_id == plantilla.id)
    )
    if solo_activas:
        q = q.filter(InboundPlantillaProveedorLinea.activo == 1)

    return q.order_by(InboundPlantillaProveedorLinea.id.asc()).all()


def crear_linea_plantilla_proveedor(
    db: Session,
    *,
    negocio_id: int,
    plantilla_id: int,
    producto_id: int,
    descripcion: Optional[str] = None,
    sku_proveedor: Optional[str] = None,
    ean13: Optional[str] = None,
    unidad: Optional[str] = None,
    activo: bool = True,
) -> InboundPlantillaProveedorLinea:
    plantilla = obtener_plantilla_segura(db, negocio_id, plantilla_id)
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
        raise InboundDomainError(f"El producto '{producto.nombre}' ya existe en la plantilla.")

    linea = InboundPlantillaProveedorLinea(
        plantilla_id=plantilla.id,
        producto_id=producto.id,
        descripcion=_norm_str(descripcion),
        sku_proveedor=_norm_str(sku_proveedor),
        ean13=_norm_str(ean13),
        unidad=_norm_str(unidad),
        activo=1 if activo else 0,
    )

    try:
        db.add(linea)
        db.commit()
        db.refresh(linea)
        return linea
    except IntegrityError:
        db.rollback()
        raise InboundDomainError("No se pudo crear la línea (posible duplicado).")


def cambiar_estado_linea_plantilla_proveedor(
    db: Session,
    *,
    negocio_id: int,
    linea_id: int,
    activo: bool,
) -> InboundPlantillaProveedorLinea:
    linea = obtener_linea_plantilla_segura(db, negocio_id, linea_id)
    linea.activo = 1 if activo else 0
    db.commit()
    db.refresh(linea)
    return linea


def eliminar_linea_plantilla_proveedor(db: Session, *, negocio_id: int, linea_id: int) -> None:
    linea = obtener_linea_plantilla_segura(db, negocio_id, linea_id)
    db.delete(linea)
    db.commit()


def reemplazar_lineas_plantilla_proveedor(
    db: Session,
    *,
    negocio_id: int,
    plantilla_id: int,
    nuevas_lineas: Iterable[dict[str, Any]],
) -> None:
    """
    Reemplazo masivo (opcional, queda útil para futuros import/plantillas).
    El dict esperado por línea:
    {
      "producto_id": int,
      "descripcion": str?,
      "sku_proveedor": str?,
      "ean13": str?,
      "unidad": str?,
      "activo": bool?
    }
    """
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
                descripcion=_norm_str(data.get("descripcion")),
                sku_proveedor=_norm_str(data.get("sku_proveedor")),
                ean13=_norm_str(data.get("ean13")),
                unidad=_norm_str(data.get("unidad")),
                activo=1 if bool(data.get("activo", True)) else 0,
            )
            db.add(linea)

        db.commit()

    except InboundDomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
