from __future__ import annotations
import enum

# =========================
# Operación WMS / Inbound
# =========================

class RecepcionOrigen(str, enum.Enum):
    CITA = "CITA"
    MANUAL = "MANUAL"


class RecepcionEstado(str, enum.Enum):
    PRE_REGISTRADO = "PRE_REGISTRADO"
    EN_ESPERA = "EN_ESPERA"
    EN_DESCARGA = "EN_DESCARGA"
    EN_CONTROL_CALIDAD = "EN_CONTROL_CALIDAD"
    CERRADO = "CERRADO"
    CANCELADO = "CANCELADO"


class PalletEstado(str, enum.Enum):
    ABIERTO = "ABIERTO"
    EN_PROCESO = "EN_PROCESO"
    LISTO = "LISTO"
    BLOQUEADO = "BLOQUEADO"


class IncidenciaEstado(str, enum.Enum):
    CREADA = "CREADA"
    EN_ANALISIS = "EN_ANALISIS"
    CERRADA = "CERRADA"
    CANCELADA = "CANCELADA"


class CitaEstado(str, enum.Enum):
    PROGRAMADA = "PROGRAMADA"
    ARRIBADO = "ARRIBADO"
    RETRASADO = "RETRASADO"
    CANCELADA = "CANCELADA"
    COMPLETADA = "COMPLETADA"


# =========================
# Inbound Documentos
# =========================

class InboundDocumentoTipo(str, enum.Enum):
    GUIA = "GUIA"
    BL = "BL"
    FACTURA = "FACTURA"
    CERTIFICADO = "CERTIFICADO"
    OTRO = "OTRO"


class InboundDocumentoEstado(str, enum.Enum):
    VIGENTE = "VIGENTE"
    REEMPLAZADO = "REEMPLAZADO"
    ANULADO = "ANULADO"


# =========================
# Inbound Fotos
# =========================

class InboundFotoTipo(str, enum.Enum):
    GENERAL = "GENERAL"
    INCIDENCIA = "INCIDENCIA"
    SELLO = "SELLO"
    PLACA = "PLACA"
    PRODUCTO = "PRODUCTO"
    TEMPERATURA = "TEMPERATURA"
    OTRO = "OTRO"


# =========================
# SaaS – ORBION
# =========================

class TenantType(str, enum.Enum):
    CUSTOMER = "customer"
    SYSTEM = "system"


class ModuleKey(str, enum.Enum):
    INBOUND = "inbound"
    WMS = "wms"


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class SubscriptionSource(str, enum.Enum):
    LEGACY = "legacy"
    MANUAL = "manual"
    STRIPE = "stripe"


class NegocioEstado(str, enum.Enum):
    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"
    BLOQUEADO = "bloqueado"


# =========================
# Inbound Checklist (SIMPLE V2)
# =========================

class InboundChecklistItemEstado(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    CUMPLE = "CUMPLE"
    NO_CUMPLE = "NO_CUMPLE"
    NA = "NA"
