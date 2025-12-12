# modules/inbound_orbion/services/services_inbound.py
"""
Fachada para mantener compatibilidad hacia atrás.

Puedes seguir importando desde:
    from modules.inbound_orbion.services.services_inbound import (...)

pero por debajo todo está organizado en archivos separados.
"""

from .services_inbound_core import (
    InboundDomainError,
    InboundConfig,
    obtener_recepcion_segura,
    validar_recepcion_editable,
    validar_producto_para_negocio,
)

from .services_inbound_lineas import (
    crear_linea_inbound,
    actualizar_linea_inbound,
    eliminar_linea_inbound,
)

from .services_inbound_incidencias import (
    crear_incidencia_inbound,
    eliminar_incidencia_inbound,
)

from .services_inbound_analytics import (
    calcular_metricas_recepcion,
    calcular_metricas_negocio,
)
