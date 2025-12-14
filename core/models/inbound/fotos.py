# core/models/inbound/fotos.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


class InboundFoto(Base):
    """
    Evidencia fotográfica inbound.
    Baseline v1: guardamos metadata + referencia (path/url).
    El storage real (local/S3/Azure Blob) se implementa en services.
    """
    __tablename__ = "inbound_fotos"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    # scope: puede colgar de recepcion / linea / incidencia / pallet
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=True)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), index=True, nullable=True)
    incidencia_id = Column(Integer, ForeignKey("inbound_incidencias.id"), index=True, nullable=True)
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=True)

    # metadata
    titulo = Column(String, nullable=True)
    nota = Column(Text, nullable=True)

    # referencia al archivo
    archivo_url = Column(String, nullable=True)   # ej: https://... o /static/uploads/...
    archivo_path = Column(String, nullable=True)  # ej: ruta relativa interna si usas filesystem
    mime_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # relaciones (strings para evitar circular import)
    recepcion = relationship("InboundRecepcion", back_populates="fotos")
    linea = relationship("InboundLinea", back_populates="fotos")
    incidencia = relationship("InboundIncidencia", back_populates="fotos")
    pallet = relationship("InboundPallet", back_populates="fotos")
