
# ============================
#   DICCIONARIO PLANES
# ============================

PLANES_CONFIG = {
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