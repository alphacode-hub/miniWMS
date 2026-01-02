# core/models/inbound/documentos.py
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.enums import InboundDocumentoEstado, InboundDocumentoTipo
from core.models.time import utcnow


class InboundDocumento(Base):
    """
    Documento/evidencia asociada a una recepción inbound.

    Enterprise (baseline compatible):
    - No binario en DB (solo metadata + uri).
    - Versionado simple por grupo:
        - doc_group_id agrupa versiones del mismo documento lógico (services lo setea)
        - version incremental (1..N)
        - is_current marca la vigente
    - Estado documental:
        - VIGENTE / REEMPLAZADO / ANULADO
    - Soft delete:
        - activo (0/1) + is_deleted + deleted_at (trazabilidad)
    """

    __tablename__ = "inbound_documentos"

    id = Column(Integer, primary_key=True)

    # Multi-tenant
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    # Scope inbound
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    # Documentos por línea (opcional, enterprise)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), index=True, nullable=True)

    # (opcional futuro) documentos por pallet
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=True)

    # ===== Versionado simple =====
    # NULL permitido para no forzar lógica en el modelo.
    doc_group_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1, index=True)
    is_current = Column(Boolean, nullable=False, default=True, index=True)

    # ===== Tipo/estado (formal, sin strings sueltos) =====
    tipo = Column(
        SAEnum(InboundDocumentoTipo, name="inbound_documento_tipo"),
        nullable=False,
        index=True,
        default=InboundDocumentoTipo.OTRO,
    )

    estado = Column(
        SAEnum(InboundDocumentoEstado, name="inbound_documento_estado"),
        nullable=False,
        index=True,
        default=InboundDocumentoEstado.VIGENTE,
    )

    # ===== Metadata =====
    nombre = Column(String(255), nullable=False)  # nombre visible (ej: "Guía 1234")
    mime_type = Column(String(120), nullable=True)
    uri = Column(String(500), nullable=False)     # storage key o URL según provider/estrategia
    descripcion = Column(Text, nullable=True)

    # Autor / timestamps
    creado_por = Column(String(180), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Soft delete + flags legacy (mantener compat)
    activo = Column(Integer, default=1, nullable=False, index=True)  # 0/1 (SQLite-friendly)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    size_bytes = Column(Integer, nullable=True)
    sha256 = Column(String(64), index=True, nullable=True)


    # Relaciones
    recepcion = relationship("InboundRecepcion", back_populates="documentos")
    linea = relationship("InboundLinea", back_populates="documentos")
    pallet = relationship("InboundPallet", lazy="joined")

    # Helpers compat: si tu UI/servicios usan creado_en hoy, mantenemos acceso sin duplicar columna.
    @property
    def creado_en(self):
        return self.created_at

    __table_args__ = (
        Index("ix_inb_doc_negocio_recep", "negocio_id", "recepcion_id"),
        Index("ix_inb_doc_negocio_group", "negocio_id", "doc_group_id"),
        Index("ix_inb_doc_recep_tipo", "recepcion_id", "tipo"),
        Index("ix_inb_doc_recep_vigentes", "recepcion_id", "activo", "is_deleted", "created_at")

    )
