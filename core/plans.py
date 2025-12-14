# core/plans.py
"""
Definición de planes SaaS – ORBION (enterprise-ready)

✔ Planes tipados y normalizados
✔ Separación Core WMS / Inbound
✔ Defaults seguros
✔ Preparado para:
  - validaciones centralizadas
  - UI de planes
  - billing / upgrades futuros
"""

from __future__ import annotations

from typing import TypedDict, Literal


# =========================================================
# TIPOS (contrato enterprise)
# =========================================================

PlanTipo = Literal["demo", "free", "basic", "pro"]


class CoreWMSPlan(TypedDict):
    max_usuarios: int
    max_productos: int
    max_zonas: int
    max_ubicaciones: int
    max_slots: int
    alertas_habilitadas: bool
    exportaciones_habilitadas: bool


class InboundPlan(TypedDict):
    max_recepciones_mes: int
    max_incidencias_mes: int
    enable_inbound_analytics: bool
    enable_inbound_ml_dataset: bool


# =========================================================
# PLANES CORE WMS
# =========================================================

PLANES_CORE_WMS: dict[PlanTipo, CoreWMSPlan] = {
    "demo": {
        "max_usuarios": 1,
        "max_productos": 50,
        "max_zonas": 5,
        "max_ubicaciones": 20,
        "max_slots": 200,
        "alertas_habilitadas": False,
        "exportaciones_habilitadas": False,
    },
    "free": {
        "max_usuarios": 2,
        "max_productos": 100,
        "max_zonas": 10,
        "max_ubicaciones": 50,
        "max_slots": 500,
        "alertas_habilitadas": True,
        "exportaciones_habilitadas": True,
    },
    "basic": {
        "max_usuarios": 5,
        "max_productos": 500,
        "max_zonas": 50,
        "max_ubicaciones": 300,
        "max_slots": 2000,
        "alertas_habilitadas": True,
        "exportaciones_habilitadas": True,
    },
    "pro": {
        "max_usuarios": 20,
        "max_productos": 3000,
        "max_zonas": 200,
        "max_ubicaciones": 2000,
        "max_slots": 20000,
        "alertas_habilitadas": True,
        "exportaciones_habilitadas": True,
    },
}


# =========================================================
# PLANES INBOUND
# =========================================================

PLANES_INBOUND: dict[PlanTipo, InboundPlan] = {
    "demo": {
        "max_recepciones_mes": 50,
        "max_incidencias_mes": 200,
        "enable_inbound_analytics": True,
        "enable_inbound_ml_dataset": True,
    },
    "free": {
        "max_recepciones_mes": 200,
        "max_incidencias_mes": 1000,
        "enable_inbound_analytics": True,
        "enable_inbound_ml_dataset": True,
    },
    "basic": {
        "max_recepciones_mes": 2000,
        "max_incidencias_mes": 10000,
        "enable_inbound_analytics": True,
        "enable_inbound_ml_dataset": True,
    },
    "pro": {
        "max_recepciones_mes": 100000,
        "max_incidencias_mes": 500000,
        "enable_inbound_analytics": True,
        "enable_inbound_ml_dataset": True,
    },
}


# =========================================================
# HELPERS (API ESTABLE)
# =========================================================

def normalize_plan(plan_tipo: str | None) -> PlanTipo:
    """
    Normaliza el tipo de plan.
    """
    plan = (plan_tipo or "demo").lower()
    return plan if plan in PLANES_CORE_WMS else "demo"


def get_core_plan_config(plan_tipo: str | None) -> CoreWMSPlan:
    """
    Retorna la configuración Core WMS del plan.
    """
    plan = normalize_plan(plan_tipo)
    return PLANES_CORE_WMS[plan]


def get_inbound_plan_config(plan_tipo: str | None) -> InboundPlan:
    """
    Retorna la configuración del módulo Inbound según plan.
    """
    plan = normalize_plan(plan_tipo)
    return PLANES_INBOUND[plan]


# =========================================================
# FUTURO (documentado, no ejecutable)
# =========================================================
# - billing_status
# - límites dinámicos por contrato
# - addons por módulo
# - override manual por negocio
#
# Todo este archivo está diseñado para NO romperse
# cuando Orbion crezca a SaaS multi-módulo real.
