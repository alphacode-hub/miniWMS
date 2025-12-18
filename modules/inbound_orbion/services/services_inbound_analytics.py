from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_

from core.models.time import utcnow
from core.models.inbound.recepciones import InboundRecepcion
from core.models.inbound.lineas import InboundLinea
from core.models.inbound.incidencias import InboundIncidencia
from core.models.inbound.proveedores import Proveedor
from core.models.inbound.checklist import InboundChecklistRecepcion  # ajusta import si tu path difiere
from core.models.inbound.analytics_snapshots import InboundAnalyticsSnapshot

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError


# =========================================================
# Helpers
# =========================================================

def _dt_from_iso(s: str | None) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    # Acepta "YYYY-MM-DD" o "YYYY-MM-DDTHH:MM"
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _json_dumps(payload: dict[str, Any]) -> str:
    # Mantén estable: indent opcional; aquí sin indent para DB
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_loads(s: str) -> dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        return {}


# =========================================================
# Contrato Analytics v1 (source-of-truth)
# =========================================================

def obtener_analytics_inbound(
    db: Session,
    *,
    negocio_id: int,
    desde: datetime | None,
    hasta: datetime | None,
    proveedor_id: int | None = None,
) -> dict[str, Any]:
    """
    v1: KPIs + tablas + series para charts (sin snapshots).
    - Multi-tenant estricto: negocio_id
    - Filtros por fecha sobre InboundRecepcion.created_at (baseline simple y consistente)
    """

    # ---------- filtros ----------
    filtros = [InboundRecepcion.negocio_id == negocio_id]
    if proveedor_id:
        filtros.append(InboundRecepcion.proveedor_id == proveedor_id)

    if desde:
        filtros.append(InboundRecepcion.created_at >= desde)
    if hasta:
        filtros.append(InboundRecepcion.created_at <= hasta)

    # ---------- KPIs base ----------
    kpi_total_recepciones = (
        db.query(func.count(InboundRecepcion.id))
        .filter(*filtros)
        .scalar()
        or 0
    )

    kpi_cerradas = (
        db.query(func.count(InboundRecepcion.id))
        .filter(*filtros)
        .filter(InboundRecepcion.fecha_cierre.isnot(None))
        .scalar()
        or 0
    )

    # kg recibido real (truth): suma de lineas.peso_recibido_kg
    # join recepciones para aplicar filtros (incluye proveedor y rango)
    kg_recibidos = (
        db.query(func.coalesce(func.sum(InboundLinea.peso_recibido_kg), 0.0))
        .join(InboundRecepcion, InboundRecepcion.id == InboundLinea.recepcion_id)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .filter(*[f for f in filtros if f.left.key != "negocio_id"])  # evita duplicar negocio_id
        .scalar()
        or 0.0
    )
    kg_recibidos = float(kg_recibidos)

    incidencias_total = (
        db.query(func.count(InboundIncidencia.id))
        .join(InboundRecepcion, InboundRecepcion.id == InboundIncidencia.recepcion_id)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .filter(*[f for f in filtros if f.left.key != "negocio_id"])
        .scalar()
        or 0
    )

    # checklist: % completado
    checklist_total = (
        db.query(func.count(InboundChecklistRecepcion.id))
        .join(InboundRecepcion, InboundRecepcion.id == InboundChecklistRecepcion.recepcion_id)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .filter(*[f for f in filtros if f.left.key != "negocio_id"])
        .scalar()
        or 0
    )
    checklist_completados = (
        db.query(func.count(InboundChecklistRecepcion.id))
        .join(InboundRecepcion, InboundRecepcion.id == InboundChecklistRecepcion.recepcion_id)
        .filter(InboundRecepcion.negocio_id == negocio_id)
        .filter(*[f for f in filtros if f.left.key != "negocio_id"])
        .filter(InboundChecklistRecepcion.estado.in_(["COMPLETADO", "CERRADO", "FINALIZADO"]))
        .scalar()
        or 0
    )
    checklist_pct = round(_safe_div(checklist_completados, checklist_total) * 100.0, 1)

    # ---------- tiempos (minutos) ----------
    # SLA: ETA -> Arribo (si hay ambos)
    sla_eta_arribo_min = _avg_minutes(db, filtros, InboundRecepcion.fecha_estimada_llegada, InboundRecepcion.fecha_arribo)
    # Operación: Arribo -> Fin descarga
    sla_arribo_descarga_min = _avg_minutes(db, filtros, InboundRecepcion.fecha_arribo, InboundRecepcion.fecha_fin_descarga)
    # Ciclo: Arribo -> Cierre
    sla_arribo_cierre_min = _avg_minutes(db, filtros, InboundRecepcion.fecha_arribo, InboundRecepcion.fecha_cierre)

    # ---------- tablas ----------
    tabla_proveedores = _tabla_por_proveedor(db, filtros, negocio_id)
    tabla_incidencias = _tabla_incidencias(db, filtros, negocio_id)

    # ---------- series charts ----------
    series_recepciones_por_estado = _series_recepciones_por_estado(db, filtros)
    series_incidencias_por_criticidad = _series_incidencias_por_criticidad(db, filtros, negocio_id)
    series_kg_por_dia = _series_kg_por_dia(db, filtros, negocio_id)

    payload = {
        "meta": {
            "negocio_id": negocio_id,
            "desde": desde.isoformat() if desde else None,
            "hasta": hasta.isoformat() if hasta else None,
            "proveedor_id": proveedor_id,
            "generado_en": utcnow().isoformat(),
            "version": "v1",
        },
        "kpis": {
            "recepciones_total": int(kpi_total_recepciones),
            "recepciones_cerradas": int(kpi_cerradas),
            "kg_recibidos": round(kg_recibidos, 3),
            "incidencias_total": int(incidencias_total),
            "checklist_pct": float(checklist_pct),
            "sla_eta_arribo_min": sla_eta_arribo_min,
            "sla_arribo_descarga_min": sla_arribo_descarga_min,
            "sla_arribo_cierre_min": sla_arribo_cierre_min,
        },
        "tablas": {
            "por_proveedor": tabla_proveedores,
            "incidencias": tabla_incidencias,
        },
        "series": {
            "recepciones_por_estado": series_recepciones_por_estado,
            "incidencias_por_criticidad": series_incidencias_por_criticidad,
            "kg_por_dia": series_kg_por_dia,
        },
    }
    return payload


def _avg_minutes(db: Session, filtros: list, col_ini, col_fin) -> float:
    """
    Promedio de minutos entre col_fin y col_ini para InboundRecepcion.

    - Postgres: extract(epoch from (fin - ini)) / 60
    - SQLite: (julianday(fin) - julianday(ini)) * 24 * 60

    Devuelve 0.0 si no hay datos o si falla.
    """
    try:
        dialect = getattr(getattr(db, "bind", None), "dialect", None)
        name = (getattr(dialect, "name", "") or "").lower()

        q = (
            db.query(
                func.avg(
                    # SQLite
                    ((func.julianday(col_fin) - func.julianday(col_ini)) * 24.0 * 60.0)
                    if name == "sqlite"
                    # Postgres / otros con extract(epoch)
                    else (func.extract("epoch", col_fin - col_ini) / 60.0)
                )
            )
            .select_from(InboundRecepcion)
            .filter(*filtros)
            .filter(col_ini.isnot(None))
            .filter(col_fin.isnot(None))
        )

        v = q.scalar()
        if v is None:
            return 0.0

        # En SLA reales no debería ser negativo; si llega negativo por datos sucios, lo clamp a 0
        minutes = float(v)
        if minutes < 0:
            minutes = 0.0

        return round(minutes, 1)

    except Exception:
        return 0.0



def _tabla_por_proveedor(db: Session, filtros: list, negocio_id: int) -> list[dict[str, Any]]:
    # recepciones, kg, incidencias, checklist_pct, avg cierre-min
    try:
        base = (
            db.query(
                Proveedor.id.label("proveedor_id"),
                Proveedor.nombre.label("proveedor_nombre"),
                func.count(InboundRecepcion.id).label("recepciones"),
                func.coalesce(func.sum(InboundLinea.peso_recibido_kg), 0.0).label("kg"),
                func.count(func.distinct(InboundIncidencia.id)).label("incidencias"),
                func.count(func.distinct(InboundChecklistRecepcion.id)).label("chk_total"),
                func.count(
                    func.distinct(
                        case(
                            (InboundChecklistRecepcion.estado.in_(["COMPLETADO", "CERRADO", "FINALIZADO"]), InboundChecklistRecepcion.id),
                            else_=None,
                        )
                    )
                ).label("chk_ok"),
            )
            .select_from(InboundRecepcion)
            .join(Proveedor, Proveedor.id == InboundRecepcion.proveedor_id, isouter=True)
            .join(InboundLinea, InboundLinea.recepcion_id == InboundRecepcion.id, isouter=True)
            .join(InboundIncidencia, InboundIncidencia.recepcion_id == InboundRecepcion.id, isouter=True)
            .join(InboundChecklistRecepcion, InboundChecklistRecepcion.recepcion_id == InboundRecepcion.id, isouter=True)
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .filter(*[f for f in filtros if f.left.key != "negocio_id"])
            .group_by(Proveedor.id, Proveedor.nombre)
            .order_by(func.coalesce(func.sum(InboundLinea.peso_recibido_kg), 0.0).desc())
        )
        rows = base.all()

        out: list[dict[str, Any]] = []
        for r in rows:
            chk_pct = round(_safe_div(r.chk_ok or 0, r.chk_total or 0) * 100.0, 1) if (r.chk_total or 0) else 0.0
            out.append(
                {
                    "proveedor_id": r.proveedor_id,
                    "proveedor_nombre": r.proveedor_nombre or "—",
                    "recepciones": int(r.recepciones or 0),
                    "kg": round(float(r.kg or 0.0), 3),
                    "incidencias": int(r.incidencias or 0),
                    "checklist_pct": float(chk_pct),
                }
            )
        return out
    except Exception:
        return []


def _tabla_incidencias(db: Session, filtros: list, negocio_id: int) -> list[dict[str, Any]]:
    try:
        q = (
            db.query(
                InboundIncidencia.tipo,
                InboundIncidencia.criticidad,
                func.count(InboundIncidencia.id).label("total"),
            )
            .select_from(InboundIncidencia)
            .join(InboundRecepcion, InboundRecepcion.id == InboundIncidencia.recepcion_id)
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .filter(*[f for f in filtros if f.left.key != "negocio_id"])
            .group_by(InboundIncidencia.tipo, InboundIncidencia.criticidad)
            .order_by(func.count(InboundIncidencia.id).desc())
        )
        rows = q.all()
        return [{"tipo": r.tipo, "criticidad": r.criticidad, "total": int(r.total or 0)} for r in rows]
    except Exception:
        return []


def _series_recepciones_por_estado(db: Session, filtros: list) -> dict[str, Any]:
    try:
        q = (
            db.query(
                InboundRecepcion.estado,
                func.count(InboundRecepcion.id).label("total"),
            )
            .select_from(InboundRecepcion)
            .filter(*filtros)
            .group_by(InboundRecepcion.estado)
            .order_by(func.count(InboundRecepcion.id).desc())
        )
        rows = q.all()
        labels = [str(r.estado) for r in rows]
        values = [int(r.total or 0) for r in rows]
        return {"labels": labels, "values": values}
    except Exception:
        return {"labels": [], "values": []}


def _series_incidencias_por_criticidad(db: Session, filtros: list, negocio_id: int) -> dict[str, Any]:
    try:
        q = (
            db.query(
                InboundIncidencia.criticidad,
                func.count(InboundIncidencia.id).label("total"),
            )
            .select_from(InboundIncidencia)
            .join(InboundRecepcion, InboundRecepcion.id == InboundIncidencia.recepcion_id)
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .filter(*[f for f in filtros if f.left.key != "negocio_id"])
            .group_by(InboundIncidencia.criticidad)
            .order_by(func.count(InboundIncidencia.id).desc())
        )
        rows = q.all()
        return {
            "labels": [str(r.criticidad) for r in rows],
            "values": [int(r.total or 0) for r in rows],
        }
    except Exception:
        return {"labels": [], "values": []}


def _series_kg_por_dia(db: Session, filtros: list, negocio_id: int) -> dict[str, Any]:
    """
    Serie simple: kg por día usando created_at de recepción como bucket.
    (Si luego quieres: por fecha_arribo o fecha_recepcion, se agrega un switch.)
    """
    try:
        # date_trunc solo Postgres; fallback: string slice.
        # Para v1: devolvemos vacío si no soporta.
        q = (
            db.query(
                func.date(InboundRecepcion.created_at).label("dia"),
                func.coalesce(func.sum(InboundLinea.peso_recibido_kg), 0.0).label("kg"),
            )
            .select_from(InboundRecepcion)
            .join(InboundLinea, InboundLinea.recepcion_id == InboundRecepcion.id, isouter=True)
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .filter(*[f for f in filtros if f.left.key != "negocio_id"])
            .group_by(func.date(InboundRecepcion.created_at))
            .order_by(func.date(InboundRecepcion.created_at).asc())
        )
        rows = q.all()
        return {
            "labels": [str(r.dia) for r in rows],
            "values": [round(float(r.kg or 0.0), 3) for r in rows],
        }
    except Exception:
        return {"labels": [], "values": []}


# =========================================================
# v1.1 Export CSV
# =========================================================

def exportar_analytics_csv(payload: dict[str, Any]) -> str:
    """
    v1.1: Export simple en CSV (texto).
    Exporta KPIs + tabla por proveedor.
    """
    k = payload.get("kpis", {}) or {}
    proveedores = (payload.get("tablas", {}) or {}).get("por_proveedor", []) or []

    def _csv_escape(value: Any) -> str:
        s = "" if value is None else str(value)
        # CSV safe: doblar comillas y envolver en comillas si hay separadores/quotes/newlines
        if any(ch in s for ch in [",", '"', "\n", "\r"]):
            s = s.replace('"', '""')
            return f'"{s}"'
        return s

    lines: list[str] = []
    lines.append("seccion,clave,valor")

    keys = [
        "recepciones_total",
        "recepciones_cerradas",
        "kg_recibidos",
        "incidencias_total",
        "checklist_pct",
        "sla_eta_arribo_min",
        "sla_arribo_descarga_min",
        "sla_arribo_cierre_min",
    ]
    for key in keys:
        lines.append(f"kpi,{key},{_csv_escape(k.get(key, ''))}")

    lines.append("")  # separador
    lines.append("proveedores,proveedor_id,proveedor_nombre,recepciones,kg,incidencias,checklist_pct")

    for r in proveedores:
        lines.append(",".join([
            "proveedores",
            _csv_escape(r.get("proveedor_id", "")),
            _csv_escape(r.get("proveedor_nombre", "")),
            _csv_escape(r.get("recepciones", 0)),
            _csv_escape(r.get("kg", 0)),
            _csv_escape(r.get("incidencias", 0)),
            _csv_escape(r.get("checklist_pct", 0)),
        ]))

    return "\n".join(lines)



# =========================================================
# v2 Snapshots (persistencia)
# =========================================================

def crear_snapshot_analytics(
    db: Session,
    *,
    negocio_id: int,
    payload: dict[str, Any],
) -> InboundAnalyticsSnapshot:
    snap = InboundAnalyticsSnapshot(
        negocio_id=negocio_id,
        payload_json=_json_dumps(payload),
        creado_en=utcnow(),
    )
    db.add(snap)
    db.flush()
    return snap


def listar_snapshots_analytics(
    db: Session,
    *,
    negocio_id: int,
    limit: int = 30,
) -> list[InboundAnalyticsSnapshot]:
    return (
        db.query(InboundAnalyticsSnapshot)
        .filter(InboundAnalyticsSnapshot.negocio_id == negocio_id)
        .order_by(InboundAnalyticsSnapshot.creado_en.desc())
        .limit(int(limit))
        .all()
    )


def obtener_snapshot_analytics(
    db: Session,
    *,
    negocio_id: int,
    snapshot_id: int,
) -> dict[str, Any]:
    snap = (
        db.query(InboundAnalyticsSnapshot)
        .filter(InboundAnalyticsSnapshot.negocio_id == negocio_id)
        .filter(InboundAnalyticsSnapshot.id == snapshot_id)
        .first()
    )
    if not snap:
        raise InboundDomainError("Snapshot no encontrado.")
    payload = _json_loads(snap.payload_json or "{}")
    payload.setdefault("meta", {})
    payload["meta"]["snapshot_id"] = snap.id
    payload["meta"]["snapshot_creado_en"] = snap.creado_en.isoformat() if snap.creado_en else None
    payload["meta"]["version"] = "v2"
    return payload


# =========================================================
# v3 Scoring Proveedores (0-100)
# =========================================================

def calcular_scoring_proveedores(
    db: Session,
    *,
    negocio_id: int,
    desde: datetime | None,
    hasta: datetime | None,
) -> list[dict[str, Any]]:
    """
    v3: ranking de proveedores con score 0..100.
    Señales:
      - checklist_pct (más alto mejor)
      - incidencias_por_recepcion (más bajo mejor)
      - puntualidad ETA->Arribo (más bajo mejor)
      - ciclo Arribo->Cierre (más bajo mejor)

    Nota: si no hay datos, devolvemos score neutral.
    """
    # filtros base sobre recepción
    filtros = [InboundRecepcion.negocio_id == negocio_id]
    if desde:
        filtros.append(InboundRecepcion.created_at >= desde)
    if hasta:
        filtros.append(InboundRecepcion.created_at <= hasta)

    # armamos dataset por proveedor vía consulta simple + algunos agregados
    # (preferimos robustez sobre micro-optimización en v3)
    proveedores_ids = (
        db.query(func.distinct(InboundRecepcion.proveedor_id))
        .filter(*filtros)
        .all()
    )
    prov_ids = [pid[0] for pid in proveedores_ids if pid and pid[0] is not None]

    out: list[dict[str, Any]] = []

    for pid in prov_ids:
        # KPIs por proveedor en el rango
        p = db.get(Proveedor, pid)
        nombre = p.nombre if p else "—"

        recep_ids = (
            db.query(InboundRecepcion.id)
            .filter(*filtros)
            .filter(InboundRecepcion.proveedor_id == pid)
            .all()
        )
        recep_ids = [x[0] for x in recep_ids]
        n_recep = len(recep_ids)
        if n_recep == 0:
            continue

        # incidencias por recepción
        inc_total = (
            db.query(func.count(InboundIncidencia.id))
            .filter(InboundIncidencia.recepcion_id.in_(recep_ids))
            .scalar()
            or 0
        )
        inc_rate = _safe_div(float(inc_total), float(n_recep))  # incidencias/recepción

        # checklist pct
        chk_total = (
            db.query(func.count(InboundChecklistRecepcion.id))
            .filter(InboundChecklistRecepcion.recepcion_id.in_(recep_ids))
            .scalar()
            or 0
        )
        chk_ok = (
            db.query(func.count(InboundChecklistRecepcion.id))
            .filter(InboundChecklistRecepcion.recepcion_id.in_(recep_ids))
            .filter(InboundChecklistRecepcion.estado.in_(["COMPLETADO", "CERRADO", "FINALIZADO"]))
            .scalar()
            or 0
        )
        chk_pct = _safe_div(float(chk_ok), float(chk_total)) * 100.0 if chk_total else 0.0

        # puntualidad: avg(Arribo - ETA) en minutos (abs)
        eta_arribo = _avg_minutes(
            db,
            filtros + [InboundRecepcion.proveedor_id == pid],
            InboundRecepcion.fecha_estimada_llegada,
            InboundRecepcion.fecha_arribo,
        )

        # ciclo: avg(Cierre - Arribo) en minutos
        arribo_cierre = _avg_minutes(
            db,
            filtros + [InboundRecepcion.proveedor_id == pid],
            InboundRecepcion.fecha_arribo,
            InboundRecepcion.fecha_cierre,
        )

        # ---- normalización (heurística enterprise v3) ----
        # checklist: 0..100 directo
        s_chk = _clamp(_to_float(chk_pct), 0.0, 100.0)

        # incidencias rate: ideal 0.0, malo >= 2.0 por recepción
        # score decrece linealmente: 0 -> 100, 2 -> 0
        s_inc = _clamp(100.0 * (1.0 - _clamp(inc_rate / 2.0, 0.0, 1.0)), 0.0, 100.0)

        # puntualidad: ideal 0..30 min, malo >= 240 min
        s_eta = _clamp(100.0 * (1.0 - _clamp(eta_arribo / 240.0, 0.0, 1.0)), 0.0, 100.0)

        # ciclo: ideal <= 120 min, malo >= 720 min
        s_ciclo = _clamp(100.0 * (1.0 - _clamp(arribo_cierre / 720.0, 0.0, 1.0)), 0.0, 100.0)

        # pesos (v3)
        score = (
            0.40 * s_chk +
            0.25 * s_inc +
            0.20 * s_eta +
            0.15 * s_ciclo
        )
        score = round(_clamp(score, 0.0, 100.0), 1)

        out.append(
            {
                "proveedor_id": pid,
                "proveedor_nombre": nombre,
                "recepciones": n_recep,
                "checklist_pct": round(s_chk, 1),
                "inc_rate": round(inc_rate, 2),
                "eta_arribo_min": float(eta_arribo),
                "arribo_cierre_min": float(arribo_cierre),
                "score": score,
            }
        )

    # orden por score desc
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out
