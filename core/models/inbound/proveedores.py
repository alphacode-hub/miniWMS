# core/models/inbound/proveedores.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow  # ✅ baseline utcnow centralizado


class Proveedor(Base):
    __tablename__ = "proveedores"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    # Identidad proveedor
    nombre = Column(String, nullable=False, index=True)
    rut = Column(String, nullable=True, index=True)

    # Contacto / datos operativos
    contacto = Column(String, nullable=True)       # ✅ NUEVO
    email = Column(String, nullable=True)
    telefono = Column(String, nullable=True)
    direccion = Column(String, nullable=True)      # ✅ NUEVO
    observaciones = Column(String, nullable=True)  # ✅ NUEVO

    activo = Column(Integer, default=1, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)  # ✅ NUEVO

    negocio = relationship("Negocio", back_populates="proveedores")

    # Relaciones inbound
    plantillas = relationship(
        "InboundPlantillaProveedor",
        back_populates="proveedor",
        cascade="all, delete-orphan",
    )
    citas = relationship("InboundCita", back_populates="proveedor")
    recepciones = relationship("InboundRecepcion", back_populates="proveedor")
    prealertas = relationship("InboundPrealerta", back_populates="proveedor")
    plantillas_checklist = relationship("InboundPlantillaChecklist", back_populates="proveedor")
