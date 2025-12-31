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

# ✅ TZ helpers (enterprise)
from core.formatting import assume_cl_local_to_utc, to_cl_tz

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError


# ==========================================================
# Helpers
# ==========================================================

def _strip(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip()
    return s or None


def _as_utc_from_user(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Enterprise rule:
    - UI (form) manda naive => interpretamos como Chile local y guardamos UTC aware.
    - Si viene aware => la convertimos a UTC.
    """
    return assume_cl_local_to_utc(dt)


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
        .filter(InboundCita.id == int(cita_id), InboundCita.negocio_id == int(negocio_id))
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
    """
    Listado de citas.
    Nota: los filtros desde/hasta normalmente vienen desde UI (naive),
    por lo que normalizamos a UTC-aware antes de comparar con DB.
    """
    d_desde = _as_utc_from_user(desde) if desde else None
    d_hasta = _as_utc_from_user(hasta) if hasta else None

    q = (
        db.query(InboundCita)
        .options(
            joinedload(InboundCita.proveedor),
            joinedload(InboundCita.recepcion),
        )
        .filter(InboundCita.negocio_id == int(negocio_id))
    )

    if d_desde:
        q = q.filter(InboundCita.fecha_programada >= d_desde)
    if d_hasta:
        q = q.filter(InboundCita.fecha_programada <= d_hasta)
    if estado:
        q = q.filter(InboundCita.estado == estado)

    return q.order_by(InboundCita.fecha_programada.asc()).limit(int(limit)).all()


# ==========================================================
# Estado sincronizado: recepción -> cita
# ==========================================================
# ⚠️ IMPORTANTE:
# La CITA representa planificación logística.
# La RECEPCIÓN representa ejecución operativa.
# Por eso:
# - ARRIBADO se activa al primer contacto físico (EN_ESPERA).
# - La descarga y control no cambian el estado de la cita.

def estado_cita_desde_recepcion(estado_recepcion: RecepcionEstado) -> CitaEstado:
    """
    Regla oficial (enterprise):

    RECEPCIÓN → CITA
    -----------------
    PRE_REGISTRADO        → PROGRAMADA
    EN_ESPERA             → ARRIBADO
    EN_DESCARGA           → ARRIBADO
    EN_CONTROL_CALIDAD    → ARRIBADO
    CERRADO               → COMPLETADA
    CANCELADO             → CANCELADA
    """

    if estado_recepcion == RecepcionEstado.CANCELADO:
        return CitaEstado.CANCELADA

    if estado_recepcion == RecepcionEstado.CERRADO:
        return CitaEstado.COMPLETADA

    if estado_recepcion in (
        RecepcionEstado.EN_ESPERA,
        RecepcionEstado.EN_DESCARGA,
        RecepcionEstado.EN_CONTROL_CALIDAD,
    ):
        return CitaEstado.ARRIBADO

    if estado_recepcion == RecepcionEstado.PRE_REGISTRADO:
        return CitaEstado.PROGRAMADA

    # Fallback seguro (nunca debería ocurrir)
    return CitaEstado.PROGRAMADA


def sync_cita_desde_recepcion(db: Session, recepcion: InboundRecepcion) -> None:
    """
    Sincroniza estado de cita basado en estado de recepción.
    Importante: NO hace commit (se llama dentro de transacciones existentes).
    """
    if not getattr(recepcion, "cita_id", None):
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

    # ✅ idempotencia: eliminamos líneas previas de esa recepción
    db.query(InboundLinea).filter(
        InboundLinea.negocio_id == int(negocio_id),
        InboundLinea.recepcion_id == int(recepcion_id),
    ).delete(synchronize_session=False)

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

    # ✅ Guardamos en UTC aware (naive UI => Chile local => UTC)
    fecha_programada_utc = _as_utc_from_user(fecha_programada)
    if not fecha_programada_utc:
        raise InboundDomainError("Fecha programada inválida.")

    # ✅ Código basado en hora Chile (para que INB-...-1200 sea la hora real del negocio)
    fp_local = to_cl_tz(fecha_programada_utc) or fecha_programada_utc
    codigo = f"INB-{fp_local.strftime('%Y%m%d-%H%M')}"

    ref = _strip(referencia)
    nt = _strip(notas)

    # Validación proveedor (si viene)
    _get_proveedor_opcional(db, negocio_id, proveedor_id)

    # Plantilla (si viene) puede inferir proveedor
    if plantilla_id:
        plantilla = _obtener_plantilla_segura(db, negocio_id, int(plantilla_id))
        if not proveedor_id:
            proveedor_id = int(plantilla.proveedor_id)
            _get_proveedor_opcional(db, negocio_id, proveedor_id)
        if int(plantilla.proveedor_id) != int(proveedor_id):
            raise InboundDomainError("La plantilla seleccionada no pertenece al proveedor elegido.")

    # 1) Crear cita
    cita = InboundCita(
        negocio_id=int(negocio_id),
        proveedor_id=int(proveedor_id) if proveedor_id else None,
        fecha_programada=fecha_programada_utc,  # ✅ UTC aware
        referencia=ref,
        notas=nt,
        estado=CitaEstado.PROGRAMADA,
    )

    # 2) Crear recepción 1:1
    recepcion = InboundRecepcion(
        negocio_id=int(negocio_id),
        proveedor_id=int(proveedor_id) if proveedor_id else None,
        cita=cita,
        codigo_recepcion=codigo,
        documento_ref=ref,

        # ✅ ETA se guarda en UTC aware (representa la hora programada real)
        fecha_estimada_llegada=fecha_programada_utc,

        # ✅ Copiar notas de la cita a observaciones iniciales
        observaciones=nt,

        estado=RecepcionEstado.PRE_REGISTRADO,
        plantilla_id=int(plantilla_id) if plantilla_id else None,

        contenedor=_strip(contenedor),
        patente_camion=_strip(patente_camion),
        tipo_carga=_strip(tipo_carga),
    )

    try:
        db.add(cita)
        db.add(recepcion)
        db.flush()  # asegura IDs

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

    # 2) cancelar recepción 1:1
    recep = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.negocio_id == int(negocio_id),
            InboundRecepcion.cita_id == int(cita.id),
        )
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

        # sincroniza estado (aunque ya quedó cancelada, mantenemos la regla)
        sync_cita_desde_recepcion(db, recep)

    try:
        db.commit()
        db.refresh(cita)
        return cita
    except IntegrityError as exc:
        db.rollback()
        raise InboundDomainError("No se pudo cancelar la cita/recepción.") from exc
