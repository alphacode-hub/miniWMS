from __future__ import annotations

from sqlalchemy import Column, Integer, ForeignKey, DateTime, Text, Boolean
from sqlalchemy.orm import relationship, Session

from core.database import Base
from core.models.time import utcnow


class InboundConfig(Base):
    __tablename__ = "inbound_config"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), unique=True, index=True, nullable=False)

    # =========================
    # FLAGS ENTERPRISE (workflow + validaciones)
    # =========================
    require_lote = Column(Boolean, default=False, nullable=False)
    require_fecha_vencimiento = Column(Boolean, default=False, nullable=False)
    require_temperatura = Column(Boolean, default=True, nullable=False)

    permitir_editar_en_control_calidad = Column(Boolean, default=True, nullable=False)
    permitir_editar_cerrado = Column(Boolean, default=False, nullable=False)

    # =========================
    # CONFIG FLEXIBLE FUTURA
    # =========================
    reglas_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="inbound_config")

    # =========================
    # FACTORY ENTERPRISE
    # =========================
    @classmethod
    def from_negocio(cls, db: Session, negocio_id: int) -> "InboundConfig":
        """
        Obtiene o crea (idempotente) la configuración inbound del negocio.
        Este método es CLAVE para todo el módulo inbound.
        """
        cfg = db.query(cls).filter(cls.negocio_id == negocio_id).first()
        if cfg:
            return cfg

        cfg = cls(
            negocio_id=negocio_id,
            require_lote=False,
            require_fecha_vencimiento=False,
            require_temperatura=True,
            permitir_editar_en_control_calidad=True,
            permitir_editar_cerrado=False,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
        return cfg
