# core/models/inbound/documentos.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


class InboundDocumento(Base):
    """
    Documento/evidencia asociada a una recepción inbound.
    Baseline v1: metadata + ruta/uri al archivo (no subimos binario a la DB).
    """
    __tablename__ = "inbound_documentos"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    # metadata
    tipo = Column(String, nullable=True)          # ej: "guia", "factura", "certificado", "otro"
    nombre = Column(String, nullable=True)        # nombre original
    mime_type = Column(String, nullable=True)     # "application/pdf", "image/jpeg", etc.
    uri = Column(String, nullable=False)          # ruta local / S3 / blob / etc.
    descripcion = Column(Text, nullable=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    recepcion = relationship("InboundRecepcion", back_populates="documentos")
