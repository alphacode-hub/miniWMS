# core/models/inbound/checklist.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models import utcnow


class InboundChecklistItem(Base):
    __tablename__ = "inbound_checklist_items"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    nombre = Column(String, nullable=False)
    orden = Column(Integer, default=0, nullable=False)
    activo = Column(Integer, default=1, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="checklist_items_inbound")

    respuestas = relationship(
    "InboundChecklistRespuesta",
    back_populates="item",
    cascade="all, delete-orphan",
    )


class InboundChecklistRespuesta(Base):
    """
    Respuesta (evidencia) para un ítem de checklist inbound.
    Baseline v1:
    - Puede colgar de una recepción (lo normal).
    - Guarda respuesta booleana / texto / usuario / timestamp.
    """
    __tablename__ = "inbound_checklist_respuestas"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)
    checklist_item_id = Column(Integer, ForeignKey("inbound_checklist_items.id"), index=True, nullable=False)

    # quién respondió (guardamos string para evitar FK a usuarios si tu auth es flexible)
    respondido_por = Column(String, nullable=True)

    # respuesta
    ok = Column(Boolean, nullable=True)   # True/False si aplica
    valor = Column(String, nullable=True) # valor corto (ej: "12°C", "SI", "NO", "OK")
    nota = Column(Text, nullable=True)    # comentario libre

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # relaciones
    recepcion = relationship("InboundRecepcion", back_populates="checklist_respuestas")
    item = relationship("InboundChecklistItem", back_populates="respuestas")

