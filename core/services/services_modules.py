# core/services/services_modules.py
"""
Servicios de habilitación de módulos – ORBION (SaaS enterprise)

✅ Fuente única: Negocio.entitlements
✅ Legacy plan_tipo: solo fallback interno (lo resuelve services_entitlements)
✅ API estable:
   - negocio_tiene_core_wms
   - negocio_tiene_wms
   - negocio_tiene_inbound
"""

from __future__ import annotations

from core.models import Negocio
from core.services.services_entitlements import has_module


def negocio_tiene_wms(negocio: Negocio) -> bool:
    """
    Acceso al módulo WMS (trial + active).
    """
    return has_module(negocio, "wms", require_active=False)


def negocio_tiene_core_wms(negocio: Negocio) -> bool:
    """
    Backward compatible alias (histórico).
    """
    return negocio_tiene_wms(negocio)


def negocio_tiene_inbound(negocio: Negocio) -> bool:
    """
    Acceso al módulo Inbound (trial + active).
    """
    return has_module(negocio, "inbound", require_active=False)
