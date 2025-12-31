# core/models/inbound/pallets.py
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
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base
from core.models.time import utcnow
from core.models.enums import PalletEstado


class InboundPallet(Base):
    __tablename__ = "inbound_pallets"
    __table_args__ = (
        UniqueConstraint(
            "negocio_id",
            "recepcion_id",
            "codigo_pallet",
            name="uq_inbound_pallet_codigo",
        ),
        # Validaciones enterprise (no negativos)
        CheckConstraint("bultos IS NULL OR bultos >= 0", name="ck_inbound_pallet_bultos_nonneg"),
        CheckConstraint("peso_bruto_kg IS NULL OR peso_bruto_kg >= 0", name="ck_inbound_pallet_peso_bruto_nonneg"),
        CheckConstraint("peso_tara_kg IS NULL OR peso_tara_kg >= 0", name="ck_inbound_pallet_peso_tara_nonneg"),
        CheckConstraint("peso_neto_kg IS NULL OR peso_neto_kg >= 0", name="ck_inbound_pallet_peso_neto_nonneg"),
        CheckConstraint(
            "temperatura_promedio IS NULL OR temperatura_promedio > -100",
            name="ck_inbound_pallet_temp_sane",
        ),
        # Índice compuesto útil para lista/filtrado por estado
        Index("ix_inbound_pallets_negocio_recepcion_estado", "negocio_id", "recepcion_id", "estado"),
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

    # Pesos (medición real del pallet)
    peso_bruto_kg = Column(Float, nullable=True)
    peso_tara_kg = Column(Float, nullable=True)
    peso_neto_kg = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    cerrado_at = Column(DateTime(timezone=True), nullable=True)
    cerrado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)

    recepcion = relationship("InboundRecepcion", back_populates="pallets")
    items = relationship("InboundPalletItem", back_populates="pallet", cascade="all, delete-orphan")

    incidencias = relationship("InboundIncidencia", back_populates="pallet", cascade="all, delete-orphan")
    fotos = relationship("InboundFoto", back_populates="pallet", cascade="all, delete-orphan")

    # Trazabilidad (opcional pero muy útil para UI/auditoría)
    cerrado_por = relationship("Usuario", foreign_keys=[cerrado_por_id])

    @property
    def peso_calculado_neto(self) -> float | None:
        """
        Neto derivado si tienes bruto y tara.
        No reemplaza la columna: es helper para UI/validación.
        """
        if self.peso_bruto_kg is None or self.peso_tara_kg is None:
            return None
        return float(self.peso_bruto_kg - self.peso_tara_kg)


class InboundPalletItem(Base):
    __tablename__ = "inbound_pallet_items"
    __table_args__ = (
        UniqueConstraint("pallet_id", "linea_id", name="uq_inbound_palletitem_pallet_linea"),
        # Validaciones enterprise (no negativos)
        CheckConstraint("cantidad IS NULL OR cantidad >= 0", name="ck_inbound_pallet_item_cantidad_nonneg"),
        CheckConstraint("peso_kg IS NULL OR peso_kg >= 0", name="ck_inbound_pallet_item_peso_nonneg"),
        CheckConstraint(
            "cantidad_estimada IS NULL OR cantidad_estimada >= 0",
            name="ck_inbound_pallet_item_cantidad_est_nonneg",
        ),
        CheckConstraint(
            "peso_estimado_kg IS NULL OR peso_estimado_kg >= 0",
            name="ck_inbound_pallet_item_peso_est_nonneg",
        ),
        Index("ix_inbound_pallet_items_negocio_pallet", "negocio_id", "pallet_id"),
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=False)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), index=True, nullable=False)

    # ✅ Valores reales capturados en operación
    cantidad = Column(Float, nullable=True)
    peso_kg = Column(Float, nullable=True)

    # ✅ Estimados (derivados por conversión, útil para UI/analítica)
    cantidad_estimada = Column(Float, nullable=True)
    peso_estimado_kg = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    pallet = relationship("InboundPallet", back_populates="items")
    linea = relationship("InboundLinea", back_populates="pallet_items")
