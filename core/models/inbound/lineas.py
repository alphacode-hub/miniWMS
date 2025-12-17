# core/models/inbound/lineas.py
from __future__ import annotations

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

    # ======================================
    # OBJETIVO DOC (oficial: contrato)
    # ======================================
    cantidad_documento = Column(Float, nullable=True)
    cantidad_recibida = Column(Float, default=0, nullable=False)

    unidad = Column(String, nullable=True)

    # ✅ ENTERPRISE
    bultos = Column(Integer, nullable=True)

    # KG DOC (oficial si modo PESO)
    peso_kg = Column(Float, nullable=True)

    # KG RECIBIDO REAL (reconciliación desde pallets)
    peso_recibido_kg = Column(Float, nullable=True)

    temperatura_objetivo = Column(Float, nullable=True)
    temperatura_recibida = Column(Float, nullable=True)
    observaciones = Column(Text, nullable=True)

    # =========================================================
    # ✅ ENTERPRISE: Overrides de conversión (solo estimados UI)
    # =========================================================
    # Si el documento/realidad del proveedor difiere del maestro de producto
    peso_unitario_kg_override = Column(Float, nullable=True)
    unidades_por_bulto_override = Column(Integer, nullable=True)
    peso_por_bulto_kg_override = Column(Float, nullable=True)
    nombre_bulto_override = Column(String, nullable=True)

    recepcion = relationship("InboundRecepcion", back_populates="lineas")
    producto = relationship("Producto")

    pallet_items = relationship("InboundPalletItem", back_populates="linea", cascade="all, delete-orphan")

    fotos = relationship(
        "InboundFoto",
        back_populates="linea",
        cascade="all, delete-orphan",
    )
