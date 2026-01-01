# core/models/inbound/lineas.py
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    ForeignKey,
    Float,
    Text,
    DateTime,
)
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


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
    

    unidad = Column(String, nullable=True)

    # ✅ ENTERPRISE
    bultos = Column(Integer, nullable=True)

    # KG DOC (oficial si modo PESO)
    peso_kg = Column(Float, nullable=True)

    # ======================================
    # RECIBIDO REAL (DERIVADO)
    # ======================================
    # ⚠️ Enterprise: estos campos NO se editan desde UI.
    # Se recalculan por reconciliación desde pallets (InboundPalletItem).
    # KG RECIBIDO REAL (reconciliación desde pallets)
    cantidad_recibida = Column(Float, default=0, nullable=False)
    peso_recibido_kg = Column(Float, nullable=True)

    temperatura_objetivo = Column(Float, nullable=True)
    temperatura_recibida = Column(Float, nullable=True)
    observaciones = Column(Text, nullable=True)

    # =========================================================
    # ✅ ENTERPRISE: Overrides de conversión (solo estimados UI)
    # =========================================================
    peso_unitario_kg_override = Column(Float, nullable=True)
    unidades_por_bulto_override = Column(Integer, nullable=True)
    peso_por_bulto_kg_override = Column(Float, nullable=True)
    nombre_bulto_override = Column(String, nullable=True)

    # =========================================================
    # ✅ ENTERPRISE: Origen + lifecycle
    # =========================================================
    # DRAFT: precargada desde plantilla/cita
    es_draft = Column(Integer, default=0, nullable=False, index=True)  # 0/1 (SQLite-friendly)
    activo = Column(Integer, default=1, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # =========================================================
    # ✅ ENTERPRISE: Snapshots reconciliación (doc vs físico)
    # =========================================================
    estado_reconciliacion = Column(String(20), nullable=True)   # OK/FALTANTE/SOBRANTE/MIXTO/...
    cantidad_diferencia = Column(Float, nullable=True)         # fis_qty - doc_qty
    peso_diferencia_kg = Column(Float, nullable=True)          # fis_kg - doc_kg


    # Relaciones
    recepcion = relationship("InboundRecepcion", back_populates="lineas")
    producto = relationship("Producto")

    pallet_items = relationship("InboundPalletItem", back_populates="linea", cascade="all, delete-orphan")

    fotos = relationship(
        "InboundFoto",
        back_populates="linea",
        cascade="all, delete-orphan",
    )
