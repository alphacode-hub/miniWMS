# core/models/inbound/incidencias.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow
from core.models.enums import IncidenciaEstado


class InboundIncidencia(Base):
    __tablename__ = "inbound_incidencias"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    # legacy/optional: linkear a pallet si aplica (pero UI no depende de esto)
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=True)

    # ✅ NUEVO (enterprise): vincular a una línea real de la recepción (producto real sin tecleo)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), index=True, nullable=True)

    # dominio
    tipo = Column(String, nullable=False, default="GENERAL")  # DAÑO, HUMEDAD, FALTANTE, DOCUMENTO, etc.
    criticidad = Column(String, nullable=False, default="MEDIA")  # BAJA/MEDIA/ALTA/CRITICA
    estado = Column(String, nullable=False, default=IncidenciaEstado.CREADA.value)

    titulo = Column(String, nullable=True)
    detalle = Column(Text, nullable=True)

    # ✅ NUEVO: afectación cuantificable (para crédito/reposición)
    cantidad_afectada = Column(Float, nullable=True)
    unidad = Column(String, nullable=True)
    lote = Column(String, nullable=True)

    # auditoría / lifecycle
    creado_por = Column(String, nullable=True)  # email / nombre
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    resuelto_por = Column(String, nullable=True)
    resuelto_en = Column(DateTime(timezone=True), nullable=True)
    resolucion = Column(Text, nullable=True)

    cancelado_por = Column(String, nullable=True)
    cancelado_en = Column(DateTime(timezone=True), nullable=True)
    motivo_cancelacion = Column(Text, nullable=True)

    # soft delete
    activo = Column(Integer, default=1, nullable=False, index=True)
    eliminado_por = Column(String, nullable=True)
    eliminado_en = Column(DateTime(timezone=True), nullable=True)

    # relaciones
    recepcion = relationship("InboundRecepcion", back_populates="incidencias")
    pallet = relationship("InboundPallet", back_populates="incidencias")
    linea = relationship("InboundLinea")  # opcional (sin back_populates para evitar tocar otros modelos ahora)

    fotos = relationship(
        "InboundFoto",
        back_populates="incidencia",
        cascade="all, delete-orphan",
    )
