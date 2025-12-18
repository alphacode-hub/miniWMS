# core/models/inbound/incidencias.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow
from core.models.enums import IncidenciaEstado


class InboundIncidencia(Base):
    __tablename__ = "inbound_incidencias"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    # opcional: linkear a pallet si aplica a futuro
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=True)

    # dominio
    tipo = Column(String, nullable=False, default="GENERAL")  # ej: DAÑO, TEMPERATURA, FALTANTE, DOCUMENTO, etc.
    criticidad = Column(String, nullable=False, default="MEDIA")  # BAJA/MEDIA/ALTA
    estado = Column(String, nullable=False, default=IncidenciaEstado.CREADA.value)

    titulo = Column(String, nullable=True)
    detalle = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # relaciones
    recepcion = relationship("InboundRecepcion", back_populates="incidencias")
    pallet = relationship("InboundPallet", back_populates="incidencias")

    fotos = relationship(
    "InboundFoto",
    back_populates="incidencia",
    cascade="all, delete-orphan",
    )

