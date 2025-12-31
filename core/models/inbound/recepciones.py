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
from core.models.enums import RecepcionEstado, RecepcionOrigen


class InboundRecepcion(Base):
    __tablename__ = "inbound_recepciones"
    __table_args__ = (
        UniqueConstraint("negocio_id", "codigo_recepcion", name="uq_inbound_recepcion_codigo"),
        UniqueConstraint("negocio_id", "cita_id", name="uq_inbound_recepcion_cita_por_negocio"),
    )

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), index=True, nullable=True)

    # ✅ FK real hacia inbound_citas.id
    cita_id = Column(Integer, ForeignKey("inbound_citas.id"), index=True, nullable=True)

    origen = Column(
        SAEnum(RecepcionOrigen, name="recepcion_origen"),
        nullable=False,
        default=RecepcionOrigen.CITA,
        index=True,
    )

    plantilla_id = Column(Integer, nullable=True, index=True)

    codigo_recepcion = Column(String, nullable=False, index=True)
    documento_ref = Column(String, nullable=True, index=True)

    contenedor = Column(String, nullable=True, index=True)
    patente_camion = Column(String, nullable=True, index=True)
    tipo_carga = Column(String, nullable=True, index=True)
    fecha_estimada_llegada = Column(DateTime(timezone=True), nullable=True, index=True)

    estado = Column(
        SAEnum(RecepcionEstado, name="recepcion_estado"),
        default=RecepcionEstado.PRE_REGISTRADO,
        nullable=False,
        index=True,
    )

    fecha_recepcion = Column(DateTime(timezone=True), nullable=True, index=True)
    observaciones = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    fecha_arribo = Column(DateTime(timezone=True), nullable=True, index=True)
    fecha_inicio_descarga = Column(DateTime(timezone=True), nullable=True, index=True)
    fecha_fin_descarga = Column(DateTime(timezone=True), nullable=True, index=True)
    fecha_cierre = Column(DateTime(timezone=True), nullable=True, index=True)

    # Relaciones
    negocio = relationship("Negocio", back_populates="inbound_recepciones")
    proveedor = relationship("Proveedor", back_populates="recepciones")

    # ✅ 1:1 explícito (foreign_keys + back_populates)
    cita = relationship(
        "InboundCita",
        back_populates="recepcion",
        foreign_keys=[cita_id],
        uselist=False,
    )

    lineas = relationship("InboundLinea", back_populates="recepcion", cascade="all, delete-orphan")
    pallets = relationship("InboundPallet", back_populates="recepcion", cascade="all, delete-orphan")

    incidencias = relationship("InboundIncidencia", back_populates="recepcion", cascade="all, delete-orphan")
    fotos = relationship("InboundFoto", back_populates="recepcion", cascade="all, delete-orphan")
    checklist_respuestas = relationship("InboundChecklistRespuesta", back_populates="recepcion", cascade="all, delete-orphan")
    documentos = relationship("InboundDocumento", back_populates="recepcion", cascade="all, delete-orphan")
    checklist = relationship("InboundChecklistRecepcion", back_populates="recepcion", uselist=False, cascade="all, delete-orphan")
