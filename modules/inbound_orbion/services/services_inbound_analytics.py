# modules/inbound_orbion/services/services_inbound_analytics.py

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session

from core.models import InboundRecepcion


# ============================
#   MÉTRICAS / ANALYTICS BASE
# ============================

def calcular_metricas_recepcion(
    recepcion: InboundRecepcion,
) -> Dict[str, Optional[float]]:
    """
    Devuelve métricas básicas en minutos:
    - tiempo_espera: arribo -> inicio_descarga
    - tiempo_descarga: inicio_descarga -> fin_descarga
    - tiempo_total: arribo -> fin_descarga
    """
    def diff_minutes(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
        if not a or not b:
            return None
        return (b - a).total_seconds() / 60.0

    tiempo_espera = diff_minutes(
        recepcion.fecha_arribo,
        recepcion.fecha_inicio_descarga,
    )
    tiempo_descarga = diff_minutes(
        recepcion.fecha_inicio_descarga,
        recepcion.fecha_fin_descarga,
    )
    tiempo_total = diff_minutes(
        recepcion.fecha_arribo,
        recepcion.fecha_fin_descarga,
    )

    return {
        "tiempo_espera_min": tiempo_espera,
        "tiempo_descarga_min": tiempo_descarga,
        "tiempo_total_min": tiempo_total,
    }


def calcular_metricas_negocio(
    db: Session,
    negocio_id: int,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Agregador simple de métricas a nivel negocio.
    Sirve como base para dashboards / modelos predictivos.
    """
    q = db.query(InboundRecepcion).filter(
        InboundRecepcion.negocio_id == negocio_id,
    )

    if desde:
        q = q.filter(InboundRecepcion.creado_en >= desde)
    if hasta:
        q = q.filter(InboundRecepcion.creado_en <= hasta)

    recepciones: List[InboundRecepcion] = q.all()
    total = len(recepciones)

    if total == 0:
        return {
            "total_recepciones": 0,
            "promedio_tiempo_espera_min": None,
            "promedio_tiempo_descarga_min": None,
            "promedio_tiempo_total_min": None,
        }

    tiempos_espera: List[float] = []
    tiempos_descarga: List[float] = []
    tiempos_totales: List[float] = []

    for r in recepciones:
        m = calcular_metricas_recepcion(r)
        if m["tiempo_espera_min"] is not None:
            tiempos_espera.append(m["tiempo_espera_min"])
        if m["tiempo_descarga_min"] is not None:
            tiempos_descarga.append(m["tiempo_descarga_min"])
        if m["tiempo_total_min"] is not None:
            tiempos_totales.append(m["tiempo_total_min"])

    def promedio(valores: List[float]) -> Optional[float]:
        return sum(valores) / len(valores) if valores else None

    return {
        "total_recepciones": total,
        "promedio_tiempo_espera_min": promedio(tiempos_espera),
        "promedio_tiempo_descarga_min": promedio(tiempos_descarga),
        "promedio_tiempo_total_min": promedio(tiempos_totales),
    }
