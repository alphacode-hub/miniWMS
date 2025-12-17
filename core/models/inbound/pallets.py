# core/models/inbound/pallets.py #
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    Float,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base
from core.models.time import utcnow
from core.models.enums import PalletEstado


class InboundPallet(Base):
    __tablename__ = "inbound_pallets"
    __table_args__ = (
        UniqueConstraint("negocio_id", "recepcion_id", "codigo_pallet", name="uq_inbound_pallet_codigo"),
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    codigo_pallet = Column(String, nullable=False, index=True)

    estado = Column(
        SAEnum(PalletEstado, name="pallet_estado"),
        default=PalletEstado.ABIERTO,
        nullable=False,
        index=True,
    )

    # Operación
    bultos = Column(Integer, nullable=True)
    temperatura_promedio = Column(Float, nullable=True)
    observaciones = Column(Text, nullable=True)

    # Pesos
    peso_bruto_kg = Column(Float, nullable=True)
    peso_tara_kg = Column(Float, nullable=True)
    peso_neto_kg = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    cerrado_at = Column(DateTime(timezone=True), nullable=True)
    cerrado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)

    recepcion = relationship("InboundRecepcion", back_populates="pallets")
    items = relationship("InboundPalletItem", back_populates="pallet", cascade="all, delete-orphan")

    incidencias = relationship("InboundIncidencia", back_populates="pallet", cascade="all, delete-orphan")
    fotos = relationship("InboundFoto", back_populates="pallet", cascade="all, delete-orphan")


class InboundPalletItem(Base):
    __tablename__ = "inbound_pallet_items"
    __table_args__ = (
        UniqueConstraint("pallet_id", "linea_id", name="uq_inbound_palletitem_pallet_linea"),
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=False)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), index=True, nullable=False)

    cantidad = Column(Float, nullable=True)
    peso_kg = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    pallet = relationship("InboundPallet", back_populates="items")
    linea = relationship("InboundLinea", back_populates="pallet_items")
