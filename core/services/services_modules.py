# core/services/services_modules.py
"""
Servicios de habilitación de módulos – ORBION (SaaS enterprise)

✔ Lógica centralizada de features por negocio
✔ Basado en planes (core + inbound)
✔ API estable y legible
✔ Preparado para:
  - flags por negocio
  - overrides manuales
  - addons futuros
"""

from __future__ import annotations

from core.models import Negocio
from core.plans import (
    PLANES_CORE_WMS,
    PLANES_INBOUND,
    normalize_plan,
)


# ============================
# CORE WMS
# ============================

def negocio_tiene_core_wms(negocio: Negocio) -> bool:
    """
    Indica si el negocio tiene habilitado el Core WMS.

    Regla actual:
    - Si el plan existe en PLANES_CORE_WMS → habilitado

    Futuro:
    - flags por negocio (negocio.core_wms_enabled)
    - addons o contratos personalizados
    """
    plan = normalize_plan(negocio.plan_tipo)
    return plan in PLANES_CORE_WMS


# ============================
# INBOUND
# ============================

def negocio_tiene_inbound(negocio: Negocio) -> bool:
    """
    Indica si el negocio tiene habilitado el módulo Inbound.

    Regla actual:
    - Si el plan existe en PLANES_INBOUND → habilitado

    Futuro:
    - flags por negocio (negocio.inbound_enabled)
    - control por fecha / trial / consumo
    """
    plan = normalize_plan(negocio.plan_tipo)
    return plan in PLANES_INBOUND


# ============================
# HELPERS FUTUROS (DOCUMENTADOS)
# ============================
# def negocio_tiene_modulo(negocio: Negocio, modulo: str) -> bool:
#     ...
#
# def negocio_feature_habilitada(negocio: Negocio, feature: str) -> bool:
#     ...
#
# Este archivo queda como punto único de decisión
# para habilitación de módulos en todo Orbion.
