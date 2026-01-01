# core/models/inbound/checklist.py
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    Boolean,
    Text,
    UniqueConstraint,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow
from core.models.enums import InboundChecklistEstado, InboundChecklistValor


class InboundChecklistRecepcion(Base):
    __tablename__ = "inbound_checklist_recepcion"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), unique=True, index=True, nullable=False)

    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_checklist.id"), index=True, nullable=True)

    estado = Column(String(20), nullable=False, default=InboundChecklistEstado.PENDIENTE.value, index=True)
    iniciado_en = Column(DateTime(timezone=True), nullable=True)
    completado_en = Column(DateTime(timezone=True), nullable=True)

    observaciones = Column(Text, nullable=True)

    firmado_por = Column(String(180), nullable=True)
    firmado_en = Column(DateTime(timezone=True), nullable=True)

    bloqueado = Column(Boolean, nullable=False, default=False, index=True)
    motivo_bloqueo = Column(Text, nullable=True)

    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True)

    recepcion = relationship("InboundRecepcion", back_populates="checklist")
    plantilla = relationship("InboundPlantillaChecklist")

    __table_args__ = (
        CheckConstraint(
            "estado IN ('PENDIENTE','EN_PROGRESO','COMPLETADO','FIRMADO','BLOQUEADO')",
            name="ck_inb_chk_estado",
        ),
        Index("ix_inb_chk_negocio_estado", "negocio_id", "estado"),
        Index("ix_inb_chk_negocio_recep", "negocio_id", "recepcion_id"),
    )


class InboundChecklistRespuesta(Base):
    __tablename__ = "inbound_checklist_respuestas"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)
    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_checklist.id"), index=True, nullable=True)
    checklist_item_id = Column(Integer, ForeignKey("inbound_plantillas_checklist_items.id"), index=True, nullable=False)

    respondido_por = Column(String(180), nullable=True)

    # enterprise: principal
    valor = Column(String(8), nullable=False, default=InboundChecklistValor.NA.value, index=True)

    # compat legacy
    ok = Column(Boolean, nullable=True)

    nota = Column(Text, nullable=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("recepcion_id", "checklist_item_id", name="uq_inb_chk_resp_recep_item"),
        CheckConstraint("valor IN ('SI','NO','NA')", name="ck_inb_chk_resp_valor"),
        Index("ix_inb_chk_resp_negocio_recep", "negocio_id", "recepcion_id"),
    )

    recepcion = relationship("InboundRecepcion", back_populates="checklist_respuestas")
    item = relationship("InboundPlantillaChecklistItem")
    plantilla = relationship("InboundPlantillaChecklist")
