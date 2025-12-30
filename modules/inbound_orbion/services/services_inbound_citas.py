# modules/inbound_orbion/services/services_inbound_citas.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from core.models.enums import CitaEstado, RecepcionEstado
from core.models.inbound.citas import InboundCita
from core.models.inbound.recepciones import InboundRecepcion
from core.models.inbound.lineas import InboundLinea
from core.models.inbound.plantillas import (
    InboundPlantillaProveedor,
    InboundPlantillaProveedorLinea,
)
from core.models.inbound.proveedores import Proveedor

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError


# ==========================================================
# Helpers
# ==========================================================

def _strip(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip()
    return s or None


def _get_proveedor_opcional(db: Session, negocio_id: int, proveedor_id: Optional[int]) -> Optional[Proveedor]:
    if not proveedor_id:
        return None
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor or int(getattr(proveedor, "negocio_id", 0)) != int(negocio_id):
        raise InboundDomainError("Proveedor no válido para este negocio.")
    if getattr(proveedor, "activo", 1) in (0, False):
        raise InboundDomainError("El proveedor seleccionado está inactivo.")
    return proveedor


def obtener_cita_segura(db: Session, negocio_id: int, cita_id: int) -> InboundCita:
    cita = (
        db.query(InboundCita)
        .options(
            joinedload(InboundCita.proveedor),
            joinedload(InboundCita.recepcion),
        )
        .filter(InboundCita.id == cita_id, InboundCita.negocio_id == negocio_id)
        .first()
    )
    if not cita:
        raise InboundDomainError("Cita no encontrada para este negocio.")
    return cita



def _obtener_plantilla_segura(db: Session, negocio_id: int, plantilla_id: int) -> InboundPlantillaProveedor:
    tpl = (
        db.query(InboundPlantillaProveedor)
        .options(joinedload(InboundPlantillaProveedor.lineas))
        .filter(
            InboundPlantillaProveedor.id == int(plantilla_id),
            InboundPlantillaProveedor.negocio_id == int(negocio_id),
        )
        .first()
    )
    if not tpl:
        raise InboundDomainError("Plantilla no encontrada para este negocio.")
    if getattr(tpl, "activo", 1) in (0, False):
        raise InboundDomainError("La plantilla seleccionada está inactiva.")
    return tpl

def listar_citas(
    db: Session,
    negocio_id: int,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    estado: Optional[CitaEstado] = None,
    limit: int = 200,
) -> List[InboundCita]:
    q = (
        db.query(InboundCita)
        .options(
            joinedload(InboundCita.proveedor),
            joinedload(InboundCita.recepcion),
        )
        .filter(InboundCita.negocio_id == negocio_id)
    )

    if desde:
        q = q.filter(InboundCita.fecha_programada >= desde)
    if hasta:
        q = q.filter(InboundCita.fecha_programada <= hasta)
    if estado:
        q = q.filter(InboundCita.estado == estado)

    return q.order_by(InboundCita.fecha_programada.asc()).limit(limit).all()

# ==========================================================
# Estado sincronizado: recepción -> cita
# ==========================================================

def estado_cita_desde_recepcion(estado_recepcion: RecepcionEstado) -> CitaEstado:
    if estado_recepcion in (RecepcionEstado.PRE_REGISTRADO, RecepcionEstado.EN_ESPERA):
        return CitaEstado.PROGRAMADA
    if estado_recepcion in (RecepcionEstado.EN_DESCARGA, RecepcionEstado.EN_CONTROL_CALIDAD):
        return CitaEstado.ARRIBADO
    if estado_recepcion == RecepcionEstado.CERRADO:
        return CitaEstado.COMPLETADA
    if estado_recepcion == RecepcionEstado.CANCELADO:
        return CitaEstado.CANCELADA
    return CitaEstado.PROGRAMADA


def sync_cita_desde_recepcion(db: Session, recepcion: InboundRecepcion) -> None:
    if not recepcion.cita_id:
        return
    cita = db.get(InboundCita, int(recepcion.cita_id))
    if not cita:
        return
    nuevo = estado_cita_desde_recepcion(recepcion.estado)
    if cita.estado != nuevo:
        cita.estado = nuevo


# ==========================================================
# Crear cita => crea recepción 1:1 + precargar líneas desde plantilla
# ==========================================================

def _aplicar_plantilla_a_recepcion(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    plantilla_id: int,
) -> int:
    """
    Crea líneas de recepción desde InboundPlantillaProveedorLinea.
    Enterprise: líneas quedan DRAFT (sin objetivos doc).
    Idempotente: si se llama 2 veces, deja el mismo resultado.
    """

    # ✅ idempotencia: evitamos duplicados por reintentos/doble submit
    db.query(InboundLinea).filter(
        InboundLinea.negocio_id == int(negocio_id),
        InboundLinea.recepcion_id == int(recepcion_id),
    ).delete()

    lineas_tpl = (
        db.query(InboundPlantillaProveedorLinea)
        .options(joinedload(InboundPlantillaProveedorLinea.producto))
        .filter(
            InboundPlantillaProveedorLinea.plantilla_id == int(plantilla_id),
            InboundPlantillaProveedorLinea.activo == 1,
        )
        .all()
    )

    creadas = 0
    for lt in lineas_tpl:
        ln = InboundLinea(
            negocio_id=int(negocio_id),
            recepcion_id=int(recepcion_id),
            producto_id=int(lt.producto_id) if lt.producto_id is not None else None,
            descripcion=_strip(getattr(lt, "descripcion", None)),
            unidad=_strip(getattr(lt, "unidad", None)),
        )

        # Nota: lt tiene sku_proveedor/ean13 pero InboundLinea no los tiene -> no intentamos setear.
        db.add(ln)
        creadas += 1

    return creadas



def crear_cita_y_recepcion(
    db: Session,
    *,
    negocio_id: int,
    fecha_programada: datetime,
    proveedor_id: Optional[int] = None,
    referencia: Optional[str] = None,
    notas: Optional[str] = None,
    plantilla_id: Optional[int] = None,
    contenedor: Optional[str] = None,
    patente_camion: Optional[str] = None,
    tipo_carga: Optional[str] = None,
) -> Tuple[InboundCita, InboundRecepcion]:
    if not fecha_programada:
        raise InboundDomainError("La fecha programada es obligatoria.")

    ref = _strip(referencia)
    nt = _strip(notas)

    prov = _get_proveedor_opcional(db, negocio_id, proveedor_id)

    plantilla: InboundPlantillaProveedor | None = None
    if plantilla_id:
        plantilla = _obtener_plantilla_segura(db, negocio_id, int(plantilla_id))
        if not proveedor_id:
            proveedor_id = int(plantilla.proveedor_id)
            prov = _get_proveedor_opcional(db, negocio_id, proveedor_id)
        if int(plantilla.proveedor_id) != int(proveedor_id):
            raise InboundDomainError("La plantilla seleccionada no pertenece al proveedor elegido.")

    codigo = f"INB-{fecha_programada.strftime('%Y%m%d-%H%M')}"

    cita = InboundCita(
        negocio_id=int(negocio_id),
        proveedor_id=int(proveedor_id) if proveedor_id else None,
        fecha_programada=fecha_programada,
        referencia=ref,
        notas=nt,
        estado=CitaEstado.PROGRAMADA,
    )

    recepcion = InboundRecepcion(
        negocio_id=int(negocio_id),
        proveedor_id=int(proveedor_id) if proveedor_id else None,
        cita=cita,
        codigo_recepcion=codigo,
        documento_ref=ref,
        fecha_estimada_llegada=fecha_programada,
        estado=RecepcionEstado.PRE_REGISTRADO,
        plantilla_id=int(plantilla_id) if plantilla_id else None,

        # ✅ Transporte (ya existe en tu modelo)
        contenedor=_strip(contenedor),
        patente_camion=_strip(patente_camion),
        tipo_carga=_strip(tipo_carga),
    )

    try:
        db.add(cita)
        db.add(recepcion)
        db.flush()

        if plantilla_id:
            _aplicar_plantilla_a_recepcion(
                db,
                negocio_id=int(negocio_id),
                recepcion_id=int(recepcion.id),
                plantilla_id=int(plantilla_id),
            )

        db.commit()
        db.refresh(cita)
        db.refresh(recepcion)
        return cita, recepcion

    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo crear la cita/recepción (posible duplicado).") from exc


# ==========================================================
# Cambiar estado cita (manual) - NO incluye cancelación
# ==========================================================

def cambiar_estado_cita(db: Session, negocio_id: int, cita_id: int, nuevo_estado: CitaEstado) -> InboundCita:
    cita = obtener_cita_segura(db, negocio_id, cita_id)

    if cita.estado == CitaEstado.COMPLETADA:
        raise InboundDomainError("La cita ya está completada.")
    if nuevo_estado == CitaEstado.CANCELADA:
        raise InboundDomainError("Para cancelar usa cancelar_cita_y_recepcion().")

    cita.estado = nuevo_estado
    try:
        db.commit()
        db.refresh(cita)
        return cita
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo actualizar el estado de la cita.") from exc


# ==========================================================
# Cancelar cita => cancela recepción (cita manda)
# ==========================================================

def cancelar_cita_y_recepcion(
    db: Session,
    *,
    negocio_id: int,
    cita_id: int,
    motivo: str | None = None,
) -> InboundCita:
    cita = obtener_cita_segura(db, negocio_id, cita_id)

    if cita.estado == CitaEstado.COMPLETADA:
        raise InboundDomainError("No puedes cancelar una cita completada.")

    # 1) cancelar cita
    cita.estado = CitaEstado.CANCELADA

    # 2) cancelar recepción 1:1 (sin depender de lazy-load)
    recep = (
        db.query(InboundRecepcion)
        .filter(InboundRecepcion.negocio_id == int(negocio_id), InboundRecepcion.cita_id == int(cita.id))
        .first()
    )

    if recep is not None:
        if recep.estado == RecepcionEstado.CERRADO:
            raise InboundDomainError("No puedes cancelar: la recepción ya está cerrada.")

        recep.estado = RecepcionEstado.CANCELADO

        if motivo:
            obs = (getattr(recep, "observaciones", None) or "").strip()
            m = (motivo or "").strip()
            if m:
                recep.observaciones = (obs + "\n" if obs else "") + f"[CANCELACIÓN CITA] {m}"

        sync_cita_desde_recepcion(db, recep)

    try:
        db.commit()
        db.refresh(cita)
        return cita
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo cancelar la cita/recepción.") from exc
