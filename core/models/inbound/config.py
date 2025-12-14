# core/models/inbound/config.py
from __future__ import annotations

from sqlalchemy import Column, Integer, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models import utcnow


class InboundConfig(Base):
    __tablename__ = "inbound_config"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), unique=True, index=True, nullable=False)

    # Config flexible (puedes migrar a JSON cuando uses Postgres/Azure SQL)
    reglas_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="inbound_config")
