# modules/inbound_orbion/services/services_inbound.py
"""
Fachada (barrel module) para mantener compatibilidad hacia atrás.

Puedes seguir importando desde:
    from modules.inbound_orbion.services.services_inbound import (...)

pero por debajo todo está organizado en archivos separados.

✅ Enterprise notes
- Este archivo NO debe tener lógica.
- Solo re-exporta símbolos (API pública del módulo inbound).
"""

from __future__ import annotations

# =========================================================
# MODELO (nuevo split core/models/inbound/*)
# =========================================================
from core.models.inbound import InboundConfig

# =========================================================
# CORE inbound (reglas de dominio generales)
# =========================================================
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,
    validar_producto_para_negocio,
    validar_recepcion_editable,
)

# =========================================================
# LÍNEAS inbound
# =========================================================
from .services_inbound_lineas import (
    actualizar_linea_inbound,
    crear_linea_inbound,
    eliminar_linea_inbound,
)

# =========================================================
# INCIDENCIAS inbound
# =========================================================
from .services_inbound_incidencias import (
    crear_incidencia_inbound,
    eliminar_incidencia_inbound,
)

# =========================================================
# ANALYTICS / MÉTRICAS inbound
# =========================================================
from .services_inbound_analytics import (
    calcular_metricas_negocio,
    calcular_metricas_recepcion,
)

# =========================================================
# SERVICES FOTOS
# =========================================================
from .services_inbound_fotos import (
    crear_foto_inbound,
    eliminar_foto_inbound,
)

__all__ = [
    # Modelo
    "InboundConfig",
    # Core
    "InboundDomainError",
    "obtener_recepcion_segura",
    "obtener_recepcion_editable",
    "validar_recepcion_editable",
    "validar_producto_para_negocio",
    # Líneas
    "crear_linea_inbound",
    "actualizar_linea_inbound",
    "eliminar_linea_inbound",
    # Incidencias
    "crear_incidencia_inbound",
    "eliminar_incidencia_inbound",
    # Analytics
    "calcular_metricas_recepcion",
    "calcular_metricas_negocio",
    #Fotos
     "crear_foto_inbound",
     "eliminar_foto_inbound",
]
