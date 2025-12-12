# modules/inbound_orbion/services/services_inbound.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session

from core.models import (
    InboundRecepcion,
    InboundLinea,
    InboundIncidencia,
    Producto,
)


# ============================
#   EXCEPCIONES DE DOMINIO
# ============================

class InboundDomainError(Exception):
    """Error de dominio para el módulo Inbound."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ============================
#   CONFIGURACIÓN (WORKFLOWS)
# ============================

@dataclass
class InboundConfig:
    """Configuración de reglas para Inbound (por negocio)."""
    require_lote: bool = False
    require_fecha_vencimiento: bool = False
    require_temperatura: bool = False
    permitir_editar_en_control_calidad: bool = True
    permitir_editar_cerrado: bool = False

    @classmethod
    def from_negocio(cls, db: Session, negocio_id: int) -> "InboundConfig":
        """
        En el futuro: leer desde una tabla de configuración.
        Por ahora, devolvemos valores por defecto "enterprise-safe".
        """
        # TODO: leer de tabla inbound_config por negocio
        return cls(
            require_lote=False,
            require_fecha_vencimiento=False,
            require_temperatura=True,
            permitir_editar_en_control_calidad=True,
            permitir_editar_cerrado=False,
        )


# ============================
#   HELPERS DE DOMINIO
# ============================

def obtener_recepcion_segura(
    db: Session,
    recepcion_id: int,
    negocio_id: int,
) -> InboundRecepcion:
    recepcion = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.id == recepcion_id,
            InboundRecepcion.negocio_id == negocio_id,
        )
        .first()
    )
    if not recepcion:
        raise InboundDomainError("Recepción inbound no encontrada para este negocio.")
    return recepcion


def validar_recepcion_editable(
    recepcion: InboundRecepcion,
    config: InboundConfig,
) -> None:
    if recepcion.estado == "CERRADO" and not config.permitir_editar_cerrado:
        raise InboundDomainError(
            "La recepción ya fue cerrada y no puede modificarse."
        )

    if recepcion.estado == "EN_CONTROL_CALIDAD" and not config.permitir_editar_en_control_calidad:
        raise InboundDomainError(
            "La recepción está en control de calidad y no admite modificaciones."
        )


def validar_producto_para_negocio(
    db: Session,
    producto_id: int,
    negocio_id: int,
) -> Producto:
    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio_id == negocio_id,
        )
        .first()
    )
    if not producto:
        raise InboundDomainError(
            "El producto seleccionado no pertenece al negocio o no existe."
        )
    return producto


# ============================
#   LÍNEAS DE RECEPCIÓN
# ============================

def crear_linea_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    producto_id: int,
    lote: Optional[str] = None,
    fecha_vencimiento: Optional[datetime] = None,
    cantidad_esperada: Optional[float] = None,
    cantidad_recibida: Optional[float] = None,
    unidad: Optional[str] = None,
    temperatura_objetivo: Optional[float] = None,
    temperatura_recibida: Optional[float] = None,
    observaciones: Optional[str] = None,
    peso_kg: Optional[float] = None,
    bultos: Optional[int] = None,
) -> InboundLinea:
    config = InboundConfig.from_negocio(db, negocio_id)
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)
    validar_recepcion_editable(recepcion, config)

    # Validar producto
    producto = validar_producto_para_negocio(db, producto_id, negocio_id)

    # Validaciones de negocio mínimas
    if config.require_lote and not lote:
        raise InboundDomainError("El lote es obligatorio para este negocio.")

    if config.require_fecha_vencimiento and not fecha_vencimiento:
        raise InboundDomainError("La fecha de vencimiento es obligatoria.")

    if config.require_temperatura and temperatura_recibida is None:
        raise InboundDomainError(
            "La temperatura recibida es obligatoria para este negocio."
        )

    linea = InboundLinea(
        recepcion_id=recepcion.id,
        producto_id=producto.id,
        lote=lote or None,
        fecha_vencimiento=fecha_vencimiento,
        cantidad_esperada=cantidad_esperada,
        cantidad_recibida=cantidad_recibida,
        unidad=unidad or producto.unidad,
        temperatura_objetivo=temperatura_objetivo,
        temperatura_recibida=temperatura_recibida,
        observaciones=observaciones or None,
        peso_kg=peso_kg,
        bultos=bultos,
    )

    db.add(linea)
    db.commit()
    db.refresh(linea)
    return linea


def actualizar_linea_inbound(
    db: Session,
    negocio_id: int,
    linea_id: int,
    **updates: Any,
) -> InboundLinea:
    linea = db.query(InboundLinea).get(linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")

    recepcion = obtener_recepcion_segura(db, linea.recepcion_id, negocio_id)
    config = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, config)

    # Si se cambia producto_id, validarlo
    producto_id = updates.get("producto_id")
    if producto_id is not None:
        producto = validar_producto_para_negocio(db, producto_id, negocio_id)
        linea.producto_id = producto.id

    for field, value in updates.items():
        if field == "producto_id":
            continue
        if hasattr(linea, field):
            setattr(linea, field, value)

    db.commit()
    db.refresh(linea)
    return linea


def eliminar_linea_inbound(
    db: Session,
    negocio_id: int,
    linea_id: int,
) -> None:
    linea = db.query(InboundLinea).get(linea_id)
    if not linea:
        raise InboundDomainError("Línea inbound no encontrada.")

    recepcion = obtener_recepcion_segura(db, linea.recepcion_id, negocio_id)
    config = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, config)

    db.delete(linea)
    db.commit()


# ============================
#   INCIDENCIAS
# ============================

def crear_incidencia_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    tipo: str,
    criticidad: str,
    descripcion: str,
) -> InboundIncidencia:
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    if not tipo:
        raise InboundDomainError("El tipo de incidencia es obligatorio.")
    if criticidad not in ("baja", "media", "alta"):
        raise InboundDomainError("La criticidad debe ser baja, media o alta.")
    if not descripcion:
        raise InboundDomainError("La descripción de la incidencia es obligatoria.")

    incidencia = InboundIncidencia(
        recepcion_id=recepcion.id,
        tipo=tipo,
        criticidad=criticidad,
        descripcion=descripcion,
        # creado_por_id se setea desde la ruta si lo necesitas
    )
    db.add(incidencia)
    db.commit()
    db.refresh(incidencia)
    return incidencia


def eliminar_incidencia_inbound(
    db: Session,
    negocio_id: int,
    incidencia_id: int,
) -> None:
    incidencia = db.query(InboundIncidencia).get(incidencia_id)
    if not incidencia:
        raise InboundDomainError("Incidencia inbound no encontrada.")

    recepcion = obtener_recepcion_segura(db, incidencia.recepcion_id, negocio_id)
    # Config podría limitar borrado de incidencias en estados cerrados
    config = InboundConfig.from_negocio(db, negocio_id)
    validar_recepcion_editable(recepcion, config)

    db.delete(incidencia)
    db.commit()


# ============================
#   MÉTRICAS / ANALYTICS BASE
# ============================

def calcular_metricas_recepcion(
    recepcion: InboundRecepcion,
) -> Dict[str, Optional[float]]:
    """
    Devuelve métricas básicas en minutos:
    - tiempo_espera: arribo -> inicio_descarga
    - tiempo_descarga: inicio_descarga -> fin_descarga
    - tiempo_total: arribo -> fin_descarga
    """
    def diff_minutes(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
        if not a or not b:
            return None
        return (b - a).total_seconds() / 60.0

    tiempo_espera = diff_minutes(
        recepcion.fecha_arribo,
        recepcion.fecha_inicio_descarga,
    )
    tiempo_descarga = diff_minutes(
        recepcion.fecha_inicio_descarga,
        recepcion.fecha_fin_descarga,
    )
    tiempo_total = diff_minutes(
        recepcion.fecha_arribo,
        recepcion.fecha_fin_descarga,
    )

    return {
        "tiempo_espera_min": tiempo_espera,
        "tiempo_descarga_min": tiempo_descarga,
        "tiempo_total_min": tiempo_total,
    }


def calcular_metricas_negocio(
    db: Session,
    negocio_id: int,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Agregador simple de métricas a nivel negocio.
    Sirve como base para dashboards / modelos predictivos.
    """
    q = db.query(InboundRecepcion).filter(
        InboundRecepcion.negocio_id == negocio_id,
    )

    if desde:
        q = q.filter(InboundRecepcion.creado_en >= desde)
    if hasta:
        q = q.filter(InboundRecepcion.creado_en <= hasta)

    recepciones: List[InboundRecepcion] = q.all()
    total = len(recepciones)

    if total == 0:
        return {
            "total_recepciones": 0,
            "promedio_tiempo_espera_min": None,
            "promedio_tiempo_descarga_min": None,
            "promedio_tiempo_total_min": None,
        }

    tiempos_espera = []
    tiempos_descarga = []
    tiempos_totales = []

    for r in recepciones:
        m = calcular_metricas_recepcion(r)
        if m["tiempo_espera_min"] is not None:
            tiempos_espera.append(m["tiempo_espera_min"])
        if m["tiempo_descarga_min"] is not None:
            tiempos_descarga.append(m["tiempo_descarga_min"])
        if m["tiempo_total_min"] is not None:
            tiempos_totales.append(m["tiempo_total_min"])

    def promedio(valores: List[float]) -> Optional[float]:
        return sum(valores) / len(valores) if valores else None

    return {
        "total_recepciones": total,
        "promedio_tiempo_espera_min": promedio(tiempos_espera),
        "promedio_tiempo_descarga_min": promedio(tiempos_descarga),
        "promedio_tiempo_total_min": promedio(tiempos_totales),
    }
