# core/services/services_modules.py

from core.models import Negocio
from core.plans import PLANES_CORE_WMS, PLANES_INBOUND


def negocio_tiene_core_wms(negocio: Negocio) -> bool:
    """
    Por ahora asumimos que si el plan existe en PLANES_CORE_WMS,
    el WMS está habilitado. Más adelante puedes refinar esto
    con columnas booleanas específicas (core_wms_enabled, inbound_enabled).
    """
    tipo = (negocio.plan_tipo or "demo").lower()
    return tipo in PLANES_CORE_WMS


def negocio_tiene_inbound(negocio: Negocio) -> bool:
    """
    Similar a lo anterior, pero para el módulo inbound.
    """
    tipo = (negocio.plan_tipo or "demo").lower()
    return tipo in PLANES_INBOUND
