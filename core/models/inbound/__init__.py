# core/models/inbound/__init__.py
from core.models.enums import RecepcionEstado, PalletEstado, IncidenciaEstado, CitaEstado

from .config import InboundConfig
from .proveedores import Proveedor
from .plantillas import InboundPlantillaProveedor, InboundPlantillaProveedorLinea
from .citas import InboundCita
from .recepciones import InboundRecepcion
from .lineas import InboundLinea
from .pallets import InboundPallet, InboundPalletItem
from .prealertas import InboundPrealerta

# ✅ Checklist SIMPLE V2
from .checklist import (
    InboundChecklistPlantilla,
    InboundChecklistSeccion,
    InboundChecklistItem,
    InboundChecklistEjecucion,
    InboundChecklistRespuesta,
)

from .incidencias import InboundIncidencia
from .fotos import InboundFoto
from .documentos import InboundDocumento


__all__ = [
    "RecepcionEstado", "PalletEstado", "IncidenciaEstado", "CitaEstado",

    "InboundConfig",
    "Proveedor",

    "InboundPlantillaProveedor", "InboundPlantillaProveedorLinea",

    "InboundCita",
    "InboundRecepcion",
    "InboundLinea",
    "InboundPallet", "InboundPalletItem",
    "InboundPrealerta",

    # ✅ Checklist SIMPLE V2
    "InboundChecklistPlantilla",
    "InboundChecklistSeccion",
    "InboundChecklistItem",
    "InboundChecklistEjecucion",
    "InboundChecklistRespuesta",

    "InboundIncidencia",
    "InboundFoto",
    "InboundDocumento",
]
