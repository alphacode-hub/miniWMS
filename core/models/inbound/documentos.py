# core/models/inbound/documentos.py
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    Boolean,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow


class InboundDocumento(Base):
    """
    Documento/evidencia asociada a una recepción inbound.

    Enterprise (baseline compatible):
    - No binario en DB (solo metadata + uri).
    - Versionado simple:
        - doc_group_id agrupa versiones del mismo documento lógico (se setea en services)
        - version incremental (1..N)
        - is_current marca la vigente
    - Soft delete con activo + is_deleted.
    """
    __tablename__ = "inbound_documentos"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), index=True, nullable=False)

    # (opcional futuro) si quieres documentos por pallet
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), index=True, nullable=True)

    # ===== Versionado simple =====
    # OJO: NULL permitido para no forzar lógica en el modelo.
    doc_group_id = Column(String(36), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1, index=True)
    is_current = Column(Boolean, nullable=False, default=True, index=True)

    # metadata
    tipo = Column(String(32), nullable=False)          # "guia"/"factura"/... o "GUIA"/"FACTURA" según tu UI
    nombre = Column(String(255), nullable=False)
    mime_type = Column(String(120), nullable=True)
    uri = Column(String(500), nullable=False)
    descripcion = Column(Text, nullable=True)

    creado_por = Column(String(180), nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    activo = Column(Integer, default=1, nullable=False, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)

    recepcion = relationship("InboundRecepcion", back_populates="documentos")
    pallet = relationship("InboundPallet", lazy="joined")

    __table_args__ = (
        # no te obligo a mayúsculas: acepto ambos (puedes endurecer luego)
        CheckConstraint(
            "tipo IN ('GUIA','BL','FACTURA','CERTIFICADO','OTRO','guia','bl','factura','certificado','otro')",
            name="ck_inb_doc_tipo",
        ),
        Index("ix_inb_doc_negocio_recep", "negocio_id", "recepcion_id"),
        Index("ix_inb_doc_negocio_group", "negocio_id", "doc_group_id"),
    )
