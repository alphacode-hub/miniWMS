from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from core.database import Base
from core.models.time import utcnow
from core.models.enums import InboundChecklistItemEstado


# =========================================================
# Plantilla (versión)
# =========================================================

class InboundChecklistPlantilla(Base):
    """
    Checklist SIMPLE V2 (ORBION)

    - Plantilla por negocio (puedes tener varias versiones activas/inactivas)
    - Contiene secciones e ítems
    """

    __tablename__ = "inbound_checklist_plantillas"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    nombre = Column(String(140), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)

    activo = Column(Boolean, nullable=False, default=True, index=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True)

    negocio = relationship("Negocio", back_populates="plantillas_checklist")

    secciones = relationship(
        "InboundChecklistSeccion",
        back_populates="plantilla",
        cascade="all, delete-orphan",
        order_by="InboundChecklistSeccion.orden.asc(), InboundChecklistSeccion.id.asc()",
    )

    items = relationship(
        "InboundChecklistItem",
        back_populates="plantilla",
        cascade="all, delete-orphan",
        order_by="InboundChecklistItem.orden.asc(), InboundChecklistItem.id.asc()",
    )

    __table_args__ = (
        UniqueConstraint("negocio_id", "nombre", "version", name="uq_inb_chk_tpl_neg_nombre_ver"),
        Index("ix_inb_chk_tpl_neg_activo", "negocio_id", "activo"),
    )


class InboundChecklistSeccion(Base):
    """
    Sección de una plantilla (simple)
    """

    __tablename__ = "inbound_checklist_secciones"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    plantilla_id = Column(
        Integer,
        ForeignKey("inbound_checklist_plantillas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    codigo = Column(String(40), nullable=False)   # ej: DOCS, COND, TEMP
    titulo = Column(String(120), nullable=False)  # ej: "Documentos"
    orden = Column(Integer, nullable=False, default=0)

    activo = Column(Boolean, nullable=False, default=True, index=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    plantilla = relationship("InboundChecklistPlantilla", back_populates="secciones")

    items = relationship(
        "InboundChecklistItem",
        back_populates="seccion",
        cascade="all, delete-orphan",
        order_by="InboundChecklistItem.orden.asc(), InboundChecklistItem.id.asc()",
    )

    __table_args__ = (
        UniqueConstraint("plantilla_id", "codigo", name="uq_inb_chk_sec_tpl_codigo"),
        CheckConstraint("orden >= 0", name="ck_inb_chk_sec_orden_nonneg"),
        Index("ix_inb_chk_sec_tpl_orden", "plantilla_id", "orden"),
        Index("ix_inb_chk_sec_neg_activo", "negocio_id", "activo"),
    )


class InboundChecklistItem(Base):
    """
    Ítem de checklist (simple de verdad)

    - tipo NO existe: todos son estado + nota opcional
    - crítico: si NO_CUMPLE en críticos => bloqueo (regla en service)
    """

    __tablename__ = "inbound_checklist_items"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    plantilla_id = Column(
        Integer,
        ForeignKey("inbound_checklist_plantillas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seccion_id = Column(
        Integer,
        ForeignKey("inbound_checklist_secciones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    codigo = Column(String(60), nullable=False)  # estable: DOCS_ESTADO, COND_MERCADERIA, etc
    nombre = Column(String(160), nullable=False)
    descripcion = Column(Text, nullable=True)

    orden = Column(Integer, nullable=False, default=0)

    requerido = Column(Boolean, nullable=False, default=False)
    critico = Column(Boolean, nullable=False, default=False)

    activo = Column(Boolean, nullable=False, default=True, index=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    plantilla = relationship("InboundChecklistPlantilla", back_populates="items")
    seccion = relationship("InboundChecklistSeccion", back_populates="items")

    __table_args__ = (
        UniqueConstraint("plantilla_id", "codigo", name="uq_inb_chk_item_tpl_codigo"),
        CheckConstraint("orden >= 0", name="ck_inb_chk_item_orden_nonneg"),
        Index("ix_inb_chk_item_tpl_sec_orden", "plantilla_id", "seccion_id", "orden"),
        Index("ix_inb_chk_item_neg_activo", "negocio_id", "activo"),
    )


# =========================================================
# Ejecución por Recepción (1 por recepción)
# =========================================================

class InboundChecklistEjecucion(Base):
    """
    Ejecución SIMPLE V2 (1 por recepción)

    - no guarda lifecycle
    - referencia plantilla aplicada
    """

    __tablename__ = "inbound_checklist_ejecuciones"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, unique=True, index=True)

    plantilla_id = Column(Integer, ForeignKey("inbound_checklist_plantillas.id"), nullable=False, index=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True)

    recepcion = relationship("InboundRecepcion", back_populates="checklist_ejecucion")
    plantilla = relationship("InboundChecklistPlantilla")

    respuestas = relationship(
        "InboundChecklistRespuesta",
        back_populates="ejecucion",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_inb_chk_exec_neg_recep", "negocio_id", "recepcion_id"),
        Index("ix_inb_chk_exec_neg_tpl", "negocio_id", "plantilla_id"),
    )


class InboundChecklistRespuesta(Base):
    """
    Respuesta por ítem (simple)

    - 1 respuesta por (negocio, recepcion, item)
    - estado inicial: PENDIENTE
    """

    __tablename__ = "inbound_checklist_respuestas"

    id = Column(Integer, primary_key=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, index=True)

    ejecucion_id = Column(
        Integer,
        ForeignKey("inbound_checklist_ejecuciones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plantilla_id = Column(Integer, ForeignKey("inbound_checklist_plantillas.id"), nullable=False, index=True)

    item_id = Column(Integer, ForeignKey("inbound_checklist_items.id"), nullable=False, index=True)

    estado = Column(
        String(12),
        nullable=False,
        default=InboundChecklistItemEstado.PENDIENTE.value,
        index=True,
    )

    nota = Column(Text, nullable=True)

    respondido_por = Column(String(180), nullable=True)
    respondido_en = Column(DateTime(timezone=True), nullable=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True)

    recepcion = relationship("InboundRecepcion", back_populates="checklist_respuestas")
    ejecucion = relationship("InboundChecklistEjecucion", back_populates="respuestas")
    item = relationship("InboundChecklistItem")

    __table_args__ = (
        UniqueConstraint("negocio_id", "recepcion_id", "item_id", name="uq_inb_chk_resp_neg_recep_item"),
        CheckConstraint(
            "estado IN ('PENDIENTE','CUMPLE','NO_CUMPLE','NA')",
            name="ck_inb_chk_resp_estado",
        ),
        Index("ix_inb_chk_resp_neg_recep", "negocio_id", "recepcion_id"),
        Index("ix_inb_chk_resp_neg_item", "negocio_id", "item_id"),
        Index("ix_inb_chk_resp_exec", "ejecucion_id"),
    )
