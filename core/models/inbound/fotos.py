from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base
from core.models.time import utcnow
from core.models.enums import InboundFotoTipo


class InboundFoto(Base):
    """
    Evidencia fotográfica inbound (enterprise v1, baseline aligned)

    ✅ Decisión v1: gestión a nivel Recepción (recepcion_id obligatorio)
    ✅ Link opcional a Incidencia (evidencia)
    ✅ Metadata enterprise para storage: filename_original, storage_relpath, sha256, content_type, size_bytes
    ✅ Versionado simple (version)
    ✅ Soft delete por baseline (activo + eliminado_por/en)
    """

    __tablename__ = "inbound_fotos"

    id = Column(Integer, primary_key=True)

    # Multi-tenant
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    # Scope principal (v1): Recepción (obligatorio)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    # Scope opcional: evidencia de incidencia
    incidencia_id = Column(Integer, ForeignKey("inbound_incidencias.id"), index=True, nullable=True)

    # Legacy opcional (no depende la UI v1, pero no rompemos estructura)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), index=True, nullable=True)
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=True)

    # Tipo (enum enterprise)
    tipo = Column(
        SAEnum(InboundFotoTipo, name="inbound_foto_tipo"),
        nullable=False,
        default=InboundFotoTipo.GENERAL,
        index=True,
    )

    # UI metadata
    titulo = Column(String(140), nullable=True)
    nota = Column(Text, nullable=True)

    # Storage metadata (alineado a Documentos)
    filename_original = Column(String(255), nullable=False)
    storage_relpath = Column(String(500), nullable=False)  # ruta relativa dentro de STORAGE_ROOT
    content_type = Column(String(120), nullable=False)

    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=False, index=True)

    # Opcional si lo calculas en services (no obligatorio)
    width_px = Column(Integer, nullable=True)
    height_px = Column(Integer, nullable=True)

    # Versión simple
    version = Column(Integer, nullable=False, default=1)

    # Flags
    is_principal = Column(Boolean, nullable=False, default=False)

    # Auditoría baseline
    creado_por = Column(String, nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    updated_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Soft delete baseline
    activo = Column(Integer, default=1, nullable=False, index=True)
    eliminado_por = Column(String, nullable=True)
    eliminado_en = Column(DateTime(timezone=True), nullable=True)
    motivo_eliminacion = Column(String(280), nullable=True)

    # Relaciones
    recepcion = relationship("InboundRecepcion", back_populates="fotos")
    incidencia = relationship("InboundIncidencia", back_populates="fotos")

    linea = relationship("InboundLinea", back_populates="fotos")
    pallet = relationship("InboundPallet", back_populates="fotos")


# Índices útiles (enterprise)
Index("ix_inbound_fotos_recepcion_activo", InboundFoto.recepcion_id, InboundFoto.activo)
Index("ix_inbound_fotos_incidencia_activo", InboundFoto.incidencia_id, InboundFoto.activo)
Index("ix_inbound_fotos_sha256_recepcion", InboundFoto.sha256, InboundFoto.recepcion_id)
