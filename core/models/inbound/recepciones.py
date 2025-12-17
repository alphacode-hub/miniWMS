# core/models/inbound/recepciones.py
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base
from core.models.time import utcnow
from core.models.enums import RecepcionEstado


class InboundRecepcion(Base):
    __tablename__ = "inbound_recepciones"
    __table_args__ = (
        UniqueConstraint("negocio_id", "codigo_recepcion", name="uq_inbound_recepcion_codigo"),
    )

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=True)
    cita_id = Column(Integer, ForeignKey("inbound_citas.id"), index=True, nullable=True)

    # Folio interno
    codigo_recepcion = Column(String, nullable=False, index=True)

    # guía/factura/BL/OC
    documento_ref = Column(String, nullable=True, index=True)

    # ✅ CAMPOS OPERATIVOS QUE PEDISTE
    contenedor = Column(String, nullable=True, index=True)          # Ej: MSKU1234567
    patente_camion = Column(String, nullable=True, index=True)      # Ej: AB-CD12
    tipo_carga = Column(String, nullable=True, index=True)          # congelado|refrigerado|seco|palletizado|granel
    fecha_estimada_llegada = Column(DateTime(timezone=True), nullable=True, index=True)  # ETA

    estado = Column(
        SAEnum(RecepcionEstado, name="recepcion_estado"),
        default=RecepcionEstado.PRE_REGISTRADO,
        nullable=False,
        index=True,
    )

    # “fecha_recepcion” = fecha/hora real (arribo / recepción)
    fecha_recepcion = Column(DateTime(timezone=True), nullable=True, index=True)
    observaciones = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # ✅ TIMESTAMPS OPERATIVOS (para KPIs)
    fecha_arribo = Column(DateTime(timezone=True), nullable=True, index=True)
    fecha_inicio_descarga = Column(DateTime(timezone=True), nullable=True, index=True)
    fecha_fin_descarga = Column(DateTime(timezone=True), nullable=True, index=True)
    fecha_cierre = Column(DateTime(timezone=True), nullable=True, index=True)



    # Relaciones
    negocio = relationship("Negocio", back_populates="inbound_recepciones")
    proveedor = relationship("Proveedor", back_populates="recepciones")
    cita = relationship("InboundCita", back_populates="recepciones")

    lineas = relationship("InboundLinea", back_populates="recepcion", cascade="all, delete-orphan")
    pallets = relationship("InboundPallet", back_populates="recepcion", cascade="all, delete-orphan")

    incidencias = relationship("InboundIncidencia", back_populates="recepcion", cascade="all, delete-orphan")
    fotos = relationship("InboundFoto", back_populates="recepcion", cascade="all, delete-orphan")
    checklist_respuestas = relationship("InboundChecklistRespuesta", back_populates="recepcion", cascade="all, delete-orphan")
    documentos = relationship("InboundDocumento", back_populates="recepcion", cascade="all, delete-orphan")
