# core/models/inbound/plantillas.py
from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base
from core.models import utcnow


class InboundPlantillaProveedor(Base):
    __tablename__ = "inbound_plantillas_proveedor"
    __table_args__ = (
        UniqueConstraint("negocio_id", "proveedor_id", "nombre", name="uq_inbound_plantilla_prov_nombre"),
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=False)

    nombre = Column(String, nullable=False, index=True)
    activo = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="plantillas_proveedor")
    proveedor = relationship("Proveedor", back_populates="plantillas")
    lineas = relationship(
        "InboundPlantillaProveedorLinea",
        back_populates="plantilla",
        cascade="all, delete-orphan",
    )


class InboundPlantillaProveedorLinea(Base):
    __tablename__ = "inbound_plantillas_proveedor_lineas"
    __table_args__ = (
        UniqueConstraint("plantilla_id", "producto_id", name="uq_inbound_plantilla_linea_producto"),
    )

    id = Column(Integer, primary_key=True)
    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_proveedor.id"), index=True, nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id"), index=True, nullable=False)

    # Campos de ayuda / mapeo proveedor
    descripcion = Column(String, nullable=True)
    sku_proveedor = Column(String, nullable=True, index=True)
    ean13 = Column(String, nullable=True, index=True)
    unidad = Column(String, nullable=True)
    activo = Column(Integer, default=1, nullable=False)

    plantilla = relationship("InboundPlantillaProveedor", back_populates="lineas")
    producto = relationship("Producto", back_populates="plantillas_proveedor_lineas")
