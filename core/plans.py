# core/plans.py

# ============================
#   PLANES CORE MINIWMS
# ============================

PLANES_CORE_WMS = {
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

# ============================
#   PLANES MÓDULO INBOUND
# ============================

PLANES_INBOUND = {
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


def get_core_plan_config(plan_tipo: str) -> dict:
    plan = (plan_tipo or "demo").lower()
    return PLANES_CORE_WMS.get(plan, PLANES_CORE_WMS["demo"])


def get_inbound_plan_config(plan_tipo: str) -> dict:
    plan = (plan_tipo or "demo").lower()
    return PLANES_INBOUND.get(plan, PLANES_INBOUND["demo"])
