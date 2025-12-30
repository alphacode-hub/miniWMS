# core/models/inbound/citas.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base
from core.models.time import utcnow
from core.models.enums import CitaEstado


class InboundCita(Base):
    __tablename__ = "inbound_citas"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=True)

    estado = Column(
        SAEnum(CitaEstado, name="cita_estado"),
        default=CitaEstado.PROGRAMADA,
        nullable=False,
        index=True,
    )

    # Fecha/hora planificada
    fecha_programada = Column(DateTime(timezone=True), nullable=False, index=True)

    referencia = Column(String, nullable=True, index=True)
    notas = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relaciones
    negocio = relationship("Negocio", back_populates="inbound_citas")
    proveedor = relationship("Proveedor", back_populates="citas")

    # ✅ 1:1 con recepción (no lista)
    recepcion = relationship("InboundRecepcion", back_populates="cita", uselist=False)
