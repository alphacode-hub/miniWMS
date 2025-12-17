# modules/inbound_orbion/routes/routes_inbound.py
"""
Router agregador del módulo Inbound – ORBION.

✔ Punto único de montaje: /inbound
✔ Cada subrouter mantiene su propio scope funcional
✔ Preparado para versionado futuro (v1 / v2)
"""

from __future__ import annotations

from fastapi import APIRouter

from .routes_inbound_core import router as core_router
from .routes_inbound_config import router as config_router
#from .routes_inbound_analytics import router as analytics_router
from .routes_inbound_lineas import router as lineas_router
from .routes_inbound_incidencias import router as incidencias_router
from .routes_inbound_checklist import router as checklist_router
#from .routes_inbound_citas import router as citas_router
from .routes_inbound_documentos import router as documentos_router
from .routes_inbound_fotos import router as fotos_router
from .routes_inbound_pallets import router as pallets_router
from .routes_inbound_proveedores import router as proveedores_router
from .routes_inbound_recepciones import router as router_recepciones


# ============================
#   ROUTER BASE INBOUND
# ============================

router = APIRouter(
    prefix="/inbound",
    tags=["inbound"],
)

# ============================
#   SUBROUTERS
# ============================
# Todos los subrouters:
#   ✔ NO definen prefix base
#   ✔ Se montan bajo /inbound
#   ✔ Mantienen responsabilidad única

router.include_router(core_router)
router.include_router(config_router)
#router.include_router(analytics_router)
router.include_router(lineas_router)
router.include_router(incidencias_router)
router.include_router(checklist_router)
#router.include_router(citas_router)
router.include_router(documentos_router)
router.include_router(fotos_router)
router.include_router(pallets_router)
router.include_router(proveedores_router)
router.include_router(router_recepciones)
