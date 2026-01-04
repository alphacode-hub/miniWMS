"""
Modelos SaaS – ORBION (enterprise, baseline aligned)

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
from core.models.enums import ModuleKey, SubscriptionStatus, UsageCounterType


# Nombres de tipos Enum en DB (Postgres) – consistentes y explícitos
MODULE_KEY_ENUM_NAME = "module_key_enum"
SUB_STATUS_ENUM_NAME = "subscription_status_enum"
USAGE_COUNTER_TYPE_ENUM_NAME = "usage_counter_type_enum"


class SuscripcionModulo(Base):
    __tablename__ = "suscripciones_modulo"
    __table_args__ = (
        UniqueConstraint("negocio_id", "module_key", name="uq_suscripcion_modulo_negocio"),

        # ✅ El período solo es válido si ambos existen (si son NULL, no aplica)
        CheckConstraint(
            "(current_period_start IS NULL AND current_period_end IS NULL) OR (current_period_end > current_period_start)",
            name="ck_suscripcion_periodo_valido",
        ),

        # ✅ Regla clave del contrato v1:
        # Un módulo NO puede tener trial_ends_at y periodo al mismo tiempo
        CheckConstraint(
            "NOT (trial_ends_at IS NOT NULL AND current_period_start IS NOT NULL)",
            name="ck_suscripcion_trial_vs_periodo_exclusivo",
        ),

        Index("ix_subs_renewal_queue", "status", "next_renewal_at"),
        Index("ix_subs_negocio_status", "negocio_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    module_key = Column(SAEnum(ModuleKey, name=MODULE_KEY_ENUM_NAME), nullable=False, index=True)

    status = Column(
        SAEnum(SubscriptionStatus, name=SUB_STATUS_ENUM_NAME),
        nullable=False,
        index=True,
        default=SubscriptionStatus.TRIAL,
    )

    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    # ✅ Deben ser NULL en trial/inactive
    current_period_start = Column(DateTime(timezone=True), nullable=True, index=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True, index=True)

    next_renewal_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_payment_at = Column(DateTime(timezone=True), nullable=True)
    past_due_since = Column(DateTime(timezone=True), nullable=True)

    cancel_at_period_end = Column(Integer, default=0, nullable=False)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="suscripciones_modulo")

    @property
    def enabled(self) -> bool:
        # ✅ Contrato v1: acceso permitido en trial/active/past_due
        return self.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)

    @property
    def is_trial(self) -> bool:
        return self.status == SubscriptionStatus.TRIAL


class UsageCounter(Base):
    """
    Contadores de uso por negocio, módulo, métrica y período.

    - Se reinicia por período (no acumulativo)
    - Base para enforcement (soft/hard) y UX del hub
    - Multi-tenant estricto por negocio_id

    Strategy C:
    - counter_type = operational | billable
    - Billable puede igualar Operational (contrato actual).
    """

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint(
            "negocio_id",
            "module_key",
            "counter_type",
            "metric_key",
            "period_start",
            "period_end",
            name="uq_usage_counter_scope",
        ),
        CheckConstraint(
            "period_end > period_start",
            name="ck_usage_periodo_valido",
        ),
        CheckConstraint(
            "value >= 0",
            name="ck_usage_value_nonneg",
        ),
        Index(
            "ix_usage_negocio_modulo_periodo",
            "negocio_id",
            "module_key",
            "counter_type",
            "period_start",
            "period_end",
        ),
        Index(
            "ix_usage_lookup_current",
            "negocio_id",
            "module_key",
            "counter_type",
            "metric_key",
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
        SAEnum(ModuleKey, name=MODULE_KEY_ENUM_NAME),
        nullable=False,
        index=True,
    )

    counter_type = Column(
        SAEnum(UsageCounterType, name=USAGE_COUNTER_TYPE_ENUM_NAME),
        nullable=False,
        index=True,
        default=UsageCounterType.BILLABLE,
        doc="Tipo de contador: operational (insights) vs billable (límites/plan).",
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
    value = Column(Float, nullable=False, default=0.0)

    # Auditoría
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relación
    negocio = relationship("Negocio", back_populates="usage_counters")

    def add(self, delta: float) -> float:
        try:
            d = float(delta)
        except Exception:
            d = 0.0
        if d <= 0:
            return float(self.value)
        self.value = float(self.value) + d
        return float(self.value)
