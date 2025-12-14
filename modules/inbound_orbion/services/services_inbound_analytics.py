# modules/inbound_orbion/services/services_inbound_analytics.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models.inbound import InboundRecepcion


# =========================================================
# TIPOS
# =========================================================

@dataclass(frozen=True)
class MetricReceptionTimes:
    tiempo_espera_min: Optional[float]
    tiempo_descarga_min: Optional[float]
    tiempo_total_min: Optional[float]

    def as_dict(self) -> dict[str, Optional[float]]:
        return {
            "tiempo_espera_min": self.tiempo_espera_min,
            "tiempo_descarga_min": self.tiempo_descarga_min,
            "tiempo_total_min": self.tiempo_total_min,
        }


# =========================================================
# HELPERS
# =========================================================

def _diff_minutes(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    # Nota: no forzamos timezone; asumimos que el modelo es tz-aware (según tu core).
    return (b - a).total_seconds() / 60.0


def _avg(values: Iterable[float], *, redondear_a: Optional[int]) -> Optional[float]:
    vals = list(values)
    if not vals:
        return None
    value = sum(vals) / len(vals)
    if redondear_a is None:
        return value
    return round(value, redondear_a)


# =========================================================
# MÉTRICAS POR RECEPCIÓN
# =========================================================

def calcular_metricas_recepcion(recepcion: InboundRecepcion) -> dict[str, Optional[float]]:
    """
    Calcula métricas básicas de una recepción (en minutos).

    Retorna:
        - tiempo_espera_min: arribo -> inicio_descarga
        - tiempo_descarga_min: inicio_descarga -> fin_descarga
        - tiempo_total_min: arribo -> fin_descarga

    Si falta alguna fecha, el valor correspondiente será None.
    """
    t = MetricReceptionTimes(
        tiempo_espera_min=_diff_minutes(recepcion.fecha_arribo, recepcion.fecha_inicio_descarga),
        tiempo_descarga_min=_diff_minutes(recepcion.fecha_inicio_descarga, recepcion.fecha_fin_descarga),
        tiempo_total_min=_diff_minutes(recepcion.fecha_arribo, recepcion.fecha_fin_descarga),
    )
    return t.as_dict()


# =========================================================
# MÉTRICAS AGREGADAS (NEGOCIO)
# =========================================================

def calcular_metricas_negocio(
    db: Session,
    negocio_id: int,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    *,
    redondear_a: Optional[int] = 2,
) -> dict[str, Any]:
    """
    Calcula métricas agregadas a nivel negocio para el módulo inbound.

    ✅ Enterprise:
    - Filtra por negocio y rango (creado_en)
    - Agrega vía SQL (más eficiente que cargar todo)
    - Retorna promedios solo cuando existen datos válidos

    Devuelve:
        - total_recepciones
        - promedio_tiempo_espera_min
        - promedio_tiempo_descarga_min
        - promedio_tiempo_total_min
    """
    base = db.query(InboundRecepcion).filter(InboundRecepcion.negocio_id == negocio_id)

    if desde is not None:
        base = base.filter(InboundRecepcion.creado_en >= desde)
    if hasta is not None:
        base = base.filter(InboundRecepcion.creado_en <= hasta)

    total = base.count()
    if total == 0:
        return {
            "total_recepciones": 0,
            "promedio_tiempo_espera_min": None,
            "promedio_tiempo_descarga_min": None,
            "promedio_tiempo_total_min": None,
        }

    # SQLite/Postgres-friendly: julianday delta * 24 * 60
    # (Si en el futuro migras a Postgres, esto sigue funcionando en SQLite;
    # para Postgres puedes migrar a EXTRACT(EPOCH...) en un incremental.)
    def _avg_minutes(expr_start, expr_end) -> Optional[float]:
        q = (
            db.query(func.avg((func.julianday(expr_end) - func.julianday(expr_start)) * 24.0 * 60.0))
            .select_from(InboundRecepcion)
            .filter(InboundRecepcion.negocio_id == negocio_id)
        )
        if desde is not None:
            q = q.filter(InboundRecepcion.creado_en >= desde)
        if hasta is not None:
            q = q.filter(InboundRecepcion.creado_en <= hasta)

        # excluir filas con nulls
        q = q.filter(expr_start.isnot(None), expr_end.isnot(None))

        value = q.scalar()
        if value is None:
            return None
        if redondear_a is None:
            return float(value)
        return round(float(value), redondear_a)

    prom_espera = _avg_minutes(InboundRecepcion.fecha_arribo, InboundRecepcion.fecha_inicio_descarga)
    prom_descarga = _avg_minutes(InboundRecepcion.fecha_inicio_descarga, InboundRecepcion.fecha_fin_descarga)
    prom_total = _avg_minutes(InboundRecepcion.fecha_arribo, InboundRecepcion.fecha_fin_descarga)

    return {
        "total_recepciones": total,
        "promedio_tiempo_espera_min": prom_espera,
        "promedio_tiempo_descarga_min": prom_descarga,
        "promedio_tiempo_total_min": prom_total,
    }
