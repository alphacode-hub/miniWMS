# core/models/inbound/prealertas.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Date, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models import utcnow


class InboundPrealerta(Base):
    __tablename__ = "inbound_prealertas"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=True)

    referencia = Column(String, nullable=True, index=True)  # BL/OC/etc
    fecha_estimada = Column(Date, nullable=True)
    estado = Column(String, default="pendiente", nullable=False, index=True)

    datos_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="prealertas_inbound")
    proveedor = relationship("Proveedor", back_populates="prealertas")
