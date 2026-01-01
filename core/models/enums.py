# core/models/enums.py
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
    CANCELADA = "CANCELADA"  # 👈 necesario para servicios enterprise


class CitaEstado(str, enum.Enum):
    PROGRAMADA = "PROGRAMADA"
    ARRIBADO = "ARRIBADO"
    RETRASADO = "RETRASADO"
    CANCELADA = "CANCELADA"
    COMPLETADA = "COMPLETADA"


# =========================
# Inbound Checklist (enterprise)
# =========================

class InboundChecklistEstado(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    EN_PROGRESO = "EN_PROGRESO"
    COMPLETADO = "COMPLETADO"
    FIRMADO = "FIRMADO"
    BLOQUEADO = "BLOQUEADO"


class InboundChecklistValor(str, enum.Enum):
    SI = "SI"
    NO = "NO"
    NA = "NA"


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
