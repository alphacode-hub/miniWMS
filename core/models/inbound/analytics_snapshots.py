from __future__ import annotations

from sqlalchemy import Column, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


class InboundAnalyticsSnapshot(Base):
    """
    Snapshot de analytics inbound (JSON serializado) para histórico.
    Baseline v2: guardamos el payload renderizable en UI, no métricas crudas sueltas.
    """
    __tablename__ = "inbound_analytics_snapshots"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    # Rango consultado (ISO en payload, pero guardamos "cuando se creó" el snapshot)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # JSON serializado (dict) con métricas/tablas/gráficos
    payload_json = Column(Text, nullable=False)

    negocio = relationship("Negocio", back_populates="inbound_analytics_snapshots")
