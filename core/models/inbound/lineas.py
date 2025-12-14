from __future__ import annotations

from datetime import date, datetime
from sqlalchemy import Column, Integer, String, Date, ForeignKey, Float, Text
from sqlalchemy.orm import relationship

from core.database import Base


class InboundLinea(Base):
    __tablename__ = "inbound_lineas"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    producto_id = Column(Integer, ForeignKey("productos.id"), index=True, nullable=True)

    descripcion = Column(String, nullable=True)
    lote = Column(String, nullable=True, index=True)
    fecha_vencimiento = Column(Date, nullable=True, index=True)

    # ✅ Tu BD actual: cantidad_documento = cantidad esperada/documento
    cantidad_documento = Column(Float, nullable=True)
    cantidad_recibida = Column(Float, default=0, nullable=False)

    unidad = Column(String, nullable=True)

    # ✅ NUEVOS CAMPOS ENTERPRISE
    bultos = Column(Integer, nullable=True)
    peso_kg = Column(Float, nullable=True)  # UI: "kilos"
    temperatura_objetivo = Column(Float, nullable=True)
    temperatura_recibida = Column(Float, nullable=True)
    observaciones = Column(Text, nullable=True)

    recepcion = relationship("InboundRecepcion", back_populates="lineas")
    producto = relationship("Producto")

    pallet_items = relationship("InboundPalletItem", back_populates="linea", cascade="all, delete-orphan")

    fotos = relationship(
    "InboundFoto",
    back_populates="linea",
    cascade="all, delete-orphan",
    )
