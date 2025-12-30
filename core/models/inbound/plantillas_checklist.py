# core/models/inbound/plantillas_checklist.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint, Boolean, Text
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
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=True)  # opcional
    nombre = Column(String, nullable=False, index=True)

    activo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

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
    )

    id = Column(Integer, primary_key=True)
    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_checklist.id"), index=True, nullable=False)

    codigo = Column(String, nullable=False)      # estable: TEMP_CAMION, SELLOS_OK, etc
    nombre = Column(String, nullable=False)      # label UI
    descripcion = Column(Text, nullable=True)

    tipo = Column(String, nullable=False, default="BOOL")  # BOOL/TEXTO/NUMERO/FECHA/OPCION
    requerido = Column(Boolean, default=False, nullable=False)
    opciones = Column(String, nullable=True)     # CSV si tipo=OPCION

    orden = Column(Integer, default=0, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)

    plantilla = relationship("InboundPlantillaChecklist", back_populates="items")
