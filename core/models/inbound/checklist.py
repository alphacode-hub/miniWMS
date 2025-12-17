from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


class InboundChecklistRecepcion(Base):
    __tablename__ = "inbound_checklist_recepcion"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), unique=True, index=True, nullable=False)

    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_checklist.id"), index=True, nullable=True)

    estado = Column(String, nullable=False, default="PENDIENTE")
    iniciado_en = Column(DateTime(timezone=True), nullable=True)
    completado_en = Column(DateTime(timezone=True), nullable=True)

    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    recepcion = relationship("InboundRecepcion", back_populates="checklist")
    plantilla = relationship("InboundPlantillaChecklist")


class InboundChecklistRespuesta(Base):
    __tablename__ = "inbound_checklist_respuestas"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_checklist.id"), index=True, nullable=True)

    checklist_item_id = Column(Integer, ForeignKey("inbound_plantillas_checklist_items.id"), index=True, nullable=False)

    respondido_por = Column(String, nullable=True)

    ok = Column(Boolean, nullable=True)
    valor = Column(String, nullable=True)
    nota = Column(Text, nullable=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("recepcion_id", "checklist_item_id", name="uq_inb_chk_resp_recep_item"),
    )

    recepcion = relationship("InboundRecepcion", back_populates="checklist_respuestas")
    item = relationship("InboundPlantillaChecklistItem")
    plantilla = relationship("InboundPlantillaChecklist")
