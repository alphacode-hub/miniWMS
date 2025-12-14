# core/models/enums.py
from __future__ import annotations
import enum

class RecepcionEstado(str, enum.Enum):
    PRE_REGISTRADO = "PRE_REGISTRADO"
    EN_ESPERA = "EN_ESPERA"
    EN_DESCARGA = "EN_DESCARGA"
    EN_CONTROL_CALIDAD = "EN_CONTROL_CALIDAD"
    CERRADO = "CERRADO"

class PalletEstado(str, enum.Enum):
    ABIERTO = "ABIERTO"
    EN_PROCESO = "EN_PROCESO"
    LISTO = "LISTO"
    BLOQUEADO = "BLOQUEADO"

class IncidenciaEstado(str, enum.Enum):
    CREADA = "CREADA"
    EN_ANALISIS = "EN_ANALISIS"
    CERRADA = "CERRADA"

class CitaEstado(str, enum.Enum):
    PROGRAMADA = "PROGRAMADA"
    ARRIBADO = "ARRIBADO"
    RETRASADO = "RETRASADO"
    CANCELADA = "CANCELADA"
    COMPLETADA = "COMPLETADA"
