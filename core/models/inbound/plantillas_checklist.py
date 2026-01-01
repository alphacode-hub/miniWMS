# core/models/inbound/plantillas_checklist.py
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    UniqueConstraint,
    Boolean,
    Text,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


class InboundPlantillaChecklist(Base):
    """
    Plantilla de checklist operacional inbound.

    Alcance:
    - negocio_id siempre.
    - proveedor_id opcional: si viene seteado, aplica a recepciones de ese proveedor.
    """
    __tablename__ = "inbound_plantillas_checklist"
    __table_args__ = (
        UniqueConstraint("negocio_id", "proveedor_id", "nombre", name="uq_inb_chk_tpl_neg_prov_nombre"),
        Index("ix_inb_chk_tpl_negocio_activo", "negocio_id", "activo"),
        Index("ix_inb_chk_tpl_negocio_prov", "negocio_id", "proveedor_id"),
    )

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=True)

    nombre = Column(String(140), nullable=False, index=True)

    activo = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="plantillas_checklist")
    proveedor = relationship("Proveedor", back_populates="plantillas_checklist")

    items = relationship(
        "InboundPlantillaChecklistItem",
        back_populates="plantilla",
        cascade="all, delete-orphan",
        order_by="InboundPlantillaChecklistItem.orden.asc()",
    )


class InboundPlantillaChecklistItem(Base):
    __tablename__ = "inbound_plantillas_checklist_items"
    __table_args__ = (
        UniqueConstraint("plantilla_id", "codigo", name="uq_inb_chk_item_tpl_codigo"),
        CheckConstraint("orden >= 0", name="ck_inb_chk_item_orden_nonneg"),
        CheckConstraint("tipo IN ('BOOL','TEXTO','NUMERO','FECHA','OPCION')", name="ck_inb_chk_item_tipo"),
        Index("ix_inb_chk_item_tpl_orden", "plantilla_id", "orden"),
        Index("ix_inb_chk_item_negocio_activo", "negocio_id", "activo"),
    )

    id = Column(Integer, primary_key=True)

    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_checklist.id"), index=True, nullable=False)

    # multi-tenant explícito (enterprise). Se puede setear al crear items usando plantilla.negocio_id.
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    codigo = Column(String(60), nullable=False)        # estable: TEMP_CAMION, SELLOS_OK, etc
    nombre = Column(String(160), nullable=False)       # label UI
    descripcion = Column(Text, nullable=True)

    tipo = Column(String(16), nullable=False, default="BOOL")  # BOOL/TEXTO/NUMERO/FECHA/OPCION
    requerido = Column(Boolean, default=False, nullable=False)

    # CSV si tipo=OPCION (en services lo normalizamos)
    opciones = Column(String(500), nullable=True)

    orden = Column(Integer, default=0, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    plantilla = relationship("InboundPlantillaChecklist", back_populates="items")
    negocio = relationship("Negocio", lazy="joined")
