"""
Modelos SaaS – ORBION (enterprise)

✔ Suscripción por módulo (inbound/wms)
✔ Estados estándar (trial/active/past_due/suspended/cancelled)
✔ Períodos rolling mensuales (current_period_*)
✔ Multi-tenant estricto por negocio_id
✔ Constraints + unique por (negocio_id, module_key)
✔ Preparado para billing / addons / prorrateos futuros
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base
from core.models.time import utcnow
from core.models.enums import ModuleKey, SubscriptionStatus


class SuscripcionModulo(Base):
    __tablename__ = "suscripciones_modulo"
    __table_args__ = (
        # Un negocio no puede tener el mismo módulo dos veces
        UniqueConstraint("negocio_id", "module_key", name="uq_suscripcion_modulo_negocio"),
        # Integridad del período
        CheckConstraint(
            "current_period_end > current_period_start",
            name="ck_suscripcion_periodo_valido",
        ),
    )

    id = Column(Integer, primary_key=True)

    # =========================
    # MULTI-TENANT
    # =========================
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    # =========================
    # MODULO / ESTADO
    # =========================
    module_key = Column(
        SAEnum(ModuleKey, name="module_key"),
        nullable=False,
        index=True,
    )

    status = Column(
        SAEnum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        index=True,
        default=SubscriptionStatus.TRIAL,
    )

    # =========================
    # FECHAS COMERCIALES (UTC)
    # =========================
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    current_period_start = Column(DateTime(timezone=True), nullable=False, index=True)
    current_period_end = Column(DateTime(timezone=True), nullable=False, index=True)

    # Para jobs futuros de renovación (puede ser igual a current_period_end)
    next_renewal_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Pago (futuro billing)
    last_payment_at = Column(DateTime(timezone=True), nullable=True)
    past_due_since = Column(DateTime(timezone=True), nullable=True)

    # Cancelación estándar
    cancel_at_period_end = Column(Integer, default=0, nullable=False)  # 0/1 (SQLite-friendly)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Auditoría
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # =========================
    # RELACIONES
    # =========================
    negocio = relationship("Negocio", back_populates="suscripciones_modulo")

    # =========================
    # HELPERS (no DB)
    # =========================
    @property
    def enabled(self) -> bool:
        return self.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE)

    @property
    def is_trial(self) -> bool:
        return self.status == SubscriptionStatus.TRIAL


class UsageCounter(Base):
    """
    Contadores de uso por negocio, módulo, métrica y período.

    - Se reinicia por período (no acumulativo)
    - Es la base para enforcement (soft/hard) y UX del hub
    - Multi-tenant estricto por negocio_id
    """

    __tablename__ = "usage_counters"
    __table_args__ = (
        # Un contador por (negocio, módulo, métrica, período)
        UniqueConstraint(
            "negocio_id",
            "module_key",
            "metric_key",
            "period_start",
            "period_end",
            name="uq_usage_counter_scope",
        ),
        # Integridad del período
        CheckConstraint(
            "period_end > period_start",
            name="ck_usage_periodo_valido",
        ),
        # No permitir negativos
        CheckConstraint(
            "value >= 0",
            name="ck_usage_value_nonneg",
        ),
        # Índice útil para queries de “uso actual”
        Index(
            "ix_usage_negocio_modulo_periodo",
            "negocio_id",
            "module_key",
            "period_start",
            "period_end",
        ),
    )

    id = Column(Integer, primary_key=True)

    # =========================
    # MULTI-TENANT
    # =========================
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    # =========================
    # DIMENSIONES
    # =========================
    module_key = Column(
        # Enum controlado (inbound/wms), fácil de extender con migración
        # Si prefieres string libre para futuro, lo cambiamos.
        # Mantengo Enum para consistencia enterprise y data hygiene.
        # (usa SAEnum ya importado arriba en saas.py; si no lo tienes aquí, deja SAEnum)
        # -> Nota: si este bloque está en el mismo archivo que SuscripcionModulo,
        # SAEnum ya está importado arriba.
        #
        # Si te quedó este modelo en un bloque separado y no tienes SAEnum,
        # añade: from sqlalchemy.types import Enum as SAEnum
        #
        # En tu archivo actual saas.py ya lo usamos arriba.
        SAEnum(ModuleKey, name="module_key"),
        nullable=False,
        index=True,
    )

    metric_key = Column(
        String,
        nullable=False,
        index=True,
        doc="Clave de métrica (ej: recepciones_mes, movimientos_mes, evidencias_mb).",
    )

    # =========================
    # PERIODO (UTC)
    # =========================
    period_start = Column(DateTime(timezone=True), nullable=False, index=True)
    period_end = Column(DateTime(timezone=True), nullable=False, index=True)

    # =========================
    # VALOR
    # =========================
    # Usamos Float para soportar contadores y cuotas tipo MB (evidencias_mb).
    # Para métricas enteras (recepciones, movimientos), se guarda como 1.0, 2.0, ...
    value = Column(Float, nullable=False, default=0.0)

    # Auditoría
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relación
    negocio = relationship("Negocio", back_populates="usage_counters")

    # =========================
    # HELPERS (no DB)
    # =========================
    def add(self, delta: float) -> float:
        """
        Incremento in-memory (no hace commit).
        Útil para services_usage.py.
        """
        try:
            d = float(delta)
        except Exception:
            d = 0.0
        if d <= 0:
            return float(self.value)
        self.value = float(self.value) + d
        return float(self.value)