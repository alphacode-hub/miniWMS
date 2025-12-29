# modules/inbound_orbion/services/services_inbound_config.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final
import json

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Negocio, Alerta
from core.models.inbound import InboundConfig, InboundRecepcion
from core.models.time import utcnow  # ✅ baseline: utcnow centralizado
from core.services.services_entitlements import resolve_entitlements

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError


# ============================
# DEFAULTS / VALIDATION
# ============================

@dataclass(frozen=True)
class InboundConfigDefaults:
    # ✅ baseline: minutos ENTEROS (no decimales)
    sla_espera_obj_min: int = 30
    sla_descarga_obj_min: int = 60
    sla_total_obj_min: int = 120

    max_incidencias_criticas_por_recepcion: int = 2
    habilitar_alertas_sla: bool = True
    habilitar_alertas_incidencias: bool = True


DEFAULTS: Final[InboundConfigDefaults] = InboundConfigDefaults()


def _default_inbound_config_dict() -> dict[str, Any]:
    return {
        # ✅ minutos ENTEROS
        "sla_espera_obj_min": int(DEFAULTS.sla_espera_obj_min),
        "sla_descarga_obj_min": int(DEFAULTS.sla_descarga_obj_min),
        "sla_total_obj_min": int(DEFAULTS.sla_total_obj_min),

        "max_incidencias_criticas_por_recepcion": int(DEFAULTS.max_incidencias_criticas_por_recepcion),
        "habilitar_alertas_sla": bool(DEFAULTS.habilitar_alertas_sla),
        "habilitar_alertas_incidencias": bool(DEFAULTS.habilitar_alertas_incidencias),

        # Validaciones de captura (enterprise)
        "require_lote": False,
        "require_fecha_vencimiento": False,
        "require_temperatura": False,
    }


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    try:
        # tolera "30.0" o "30,0" pero lo convierte a int
        s = str(v).strip().replace(",", ".")
        if s == "":
            return None
        return int(float(s))
    except Exception:
        return None


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).strip().replace(",", "."))
    except Exception:
        return None


def _coerce_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"true", "1", "on", "yes", "si", "sí"}:
        return True
    if s in {"false", "0", "off", "no"}:
        return False
    return None




def build_inbound_plan_cfg_from_entitlements(negocio: Negocio) -> dict[str, Any]:
    """
    UI adapter baseline-aligned (ENTITLEMENTS canon):
    - toma resolve_entitlements(negocio)
    - limits anidados por módulo: ent["limits"]["inbound"]["recepciones_mes"]
    - features pueden venir en módulos (si las agregas) o por flags simples en entitlements
    """
    ent = resolve_entitlements(negocio)

    segment = ent.get("segment") or "emprendedor"
    modules = ent.get("modules") if isinstance(ent.get("modules"), dict) else {}
    inbound_mod = modules.get("inbound") if isinstance(modules.get("inbound"), dict) else {}

    limits_all = ent.get("limits") if isinstance(ent.get("limits"), dict) else {}
    inbound_limits = limits_all.get("inbound") if isinstance(limits_all.get("inbound"), dict) else {}

    # límites canon (según tu services_entitlements.py)
    max_recepciones_mes = _coerce_int(inbound_limits.get("recepciones_mes"))
    max_incidencias_mes = _coerce_int(inbound_limits.get("incidencias_mes"))

    # features (si todavía no existen como dict, quedan False)
    feats = inbound_mod.get("features") if isinstance(inbound_mod.get("features"), dict) else {}
    enable_analytics = _coerce_bool(feats.get("analytics"))
    enable_ml = _coerce_bool(feats.get("ml_dataset"))

    # fallback opcional (si alguna vez dejaste flags sueltas)
    if enable_analytics is None:
        enable_analytics = bool(ent.get("enable_inbound_analytics", False))
    if enable_ml is None:
        enable_ml = bool(ent.get("enable_inbound_ml_dataset", False))

    return {
        "segment": segment,
        # estado del módulo inbound (canon)
        "inbound": {
            "enabled": bool(inbound_mod.get("enabled", True)),
            "status": (inbound_mod.get("status") or "active"),
            "coming_soon": bool(inbound_mod.get("coming_soon", False)),
        },
        # límites canon (lo que la UI quiere mostrar)
        "max_recepciones_mes": max_recepciones_mes,
        "max_incidencias_mes": max_incidencias_mes,
        # flags para UI
        "enable_inbound_analytics": bool(enable_analytics),
        "enable_inbound_ml_dataset": bool(enable_ml),
        # por si la UI quiere renderizar todo el dict inbound
        "limits_inbound": inbound_limits,
    }


# ============================
# CONFIG CRUD
# ============================

def get_or_create_inbound_config(db: Session, negocio_id: int) -> InboundConfig:
    config = db.query(InboundConfig).filter(InboundConfig.negocio_id == negocio_id).first()
    if config:
        return config

    reglas = _default_inbound_config_dict()

    config = InboundConfig(
        negocio_id=negocio_id,
        reglas_json=json.dumps(reglas, ensure_ascii=False),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    logger.info("[INBOUND][CONFIG] Creada config por defecto negocio_id=%s", negocio_id)
    return config


def inbound_config_dict(config: InboundConfig) -> dict[str, Any]:
    if not config.reglas_json:
        return {}
    try:
        v = json.loads(config.reglas_json)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def inbound_cfg_get(cfg: dict[str, Any], key: str, default=None):
    return cfg.get(key, default)


def normalize_inbound_config_dict(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    ✅ baseline: normaliza estructura y tipos (minutos ENTEROS, bools, ints).
    Se usa para UI y para evaluación de SLA sin decimales.
    """
    out = dict(cfg or {})

    # SLA (int)
    out["sla_espera_obj_min"] = _coerce_int(out.get("sla_espera_obj_min")) or None
    out["sla_descarga_obj_min"] = _coerce_int(out.get("sla_descarga_obj_min")) or None
    out["sla_total_obj_min"] = _coerce_int(out.get("sla_total_obj_min")) or None

    # incidencias (int)
    out["max_incidencias_criticas_por_recepcion"] = _coerce_int(out.get("max_incidencias_criticas_por_recepcion")) or None

    # bools
    for k in ("habilitar_alertas_sla", "habilitar_alertas_incidencias", "require_lote", "require_fecha_vencimiento", "require_temperatura"):
        b = _coerce_bool(out.get(k))
        out[k] = bool(b) if b is not None else False

    return out


def get_inbound_config_page_context(db: Session, negocio: Negocio) -> dict[str, Any]:
    """
    ✅ Para dejar la ruta liviana: arma el contexto completo de /inbound/config.
    """
    config = get_or_create_inbound_config(db, negocio.id)
    cfg = normalize_inbound_config_dict(inbound_config_dict(config))
    plan_cfg = build_inbound_plan_cfg_from_entitlements(negocio)

    return {
        "config": config,
        "config_data": cfg,
        "plan_cfg": plan_cfg,
    }


# ============================
# LIMITS / PERMISSIONS (ENTITLEMENTS)
# ============================

def _month_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(timezone.utc)
    inicio_mes = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        fin_mes = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        fin_mes = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return inicio_mes, fin_mes


def check_inbound_recepcion_limit(db: Session, negocio: Negocio) -> None:
    """
    Verifica límite de recepciones inbound en el mes actual usando ENTITLEMENTS (canon).

    Convención baseline:
      - ent["limits"]["inbound"]["recepciones_mes"] : int | None
    """
    ent = resolve_entitlements(negocio)

    limits_all = ent.get("limits") if isinstance(ent.get("limits"), dict) else {}
    inbound_limits = limits_all.get("inbound") if isinstance(limits_all.get("inbound"), dict) else {}

    max_recepciones_mes = _coerce_int(inbound_limits.get("recepciones_mes"))
    if max_recepciones_mes is None:
        return  # sin límite definido

    inicio_mes, fin_mes = _month_bounds_utc(utcnow())

    total_mes = (
        db.query(InboundRecepcion)
        .filter(
            InboundRecepcion.negocio_id == negocio.id,
            InboundRecepcion.creado_en >= inicio_mes,
            InboundRecepcion.creado_en < fin_mes,
        )
        .count()
    )

    if total_mes >= max_recepciones_mes:
        logger.warning(
            "[INBOUND][LIMIT] negocio_id=%s total_mes=%s max=%s",
            negocio.id, total_mes, max_recepciones_mes,
        )
        raise InboundDomainError(
            f"Has alcanzado el límite de recepciones inbound ({max_recepciones_mes}) para este período."
        )


def check_inbound_ml_dataset_permission(negocio: Negocio) -> None:
    ent = resolve_entitlements(negocio)

    modules = ent.get("modules") if isinstance(ent.get("modules"), dict) else {}
    inbound_mod = modules.get("inbound") if isinstance(modules.get("inbound"), dict) else {}

    feats = inbound_mod.get("features") if isinstance(inbound_mod.get("features"), dict) else {}
    allow = _coerce_bool(feats.get("ml_dataset"))

    if allow is None:
        allow = bool(ent.get("enable_inbound_ml_dataset", False))

    if not allow:
        raise InboundDomainError(
            "Tu entitlements actual no incluye acceso al dataset avanzado para ML/IA."
        )


# ============================
# SLA / ALERTAS
# ============================

def _is_already_pending_alert(db: Session, negocio_id: int, tipo: str, mensaje: str) -> bool:
    exists = (
        db.query(Alerta.id)
        .filter(
            Alerta.negocio_id == negocio_id,
            Alerta.tipo == tipo,
            Alerta.estado == "pendiente",
            Alerta.mensaje == mensaje,
        )
        .first()
    )
    return bool(exists)


def _calcular_metricas_safe(recepcion: InboundRecepcion) -> dict[str, Any]:
    """
    ✅ enterprise-safe: si analytics aún no está en este baseline, no rompe runtime.
    """
    try:
        from modules.inbound_orbion.services.services_inbound_analytics import calcular_metricas_recepcion  # type: ignore
        m = calcular_metricas_recepcion(recepcion)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def evaluar_sla_y_generar_alertas(db: Session, negocio: Negocio, recepcion: InboundRecepcion) -> int:
    """
    Evalúa SLA e incidencias y genera alertas globales (tabla 'alertas').

    ✅ Baseline:
    - SLA en ENTEROS (minutos)
    - Mensajes sin decimales
    - No rompe si analytics no está disponible todavía
    """
    config = get_or_create_inbound_config(db, negocio.id)
    cfg = normalize_inbound_config_dict(inbound_config_dict(config))
    metrics = _calcular_metricas_safe(recepcion)

    created = 0
    mensajes: list[str] = []

    # --- SLA tiempos ---
    if bool(inbound_cfg_get(cfg, "habilitar_alertas_sla", False)):
        sla_espera = _coerce_int(inbound_cfg_get(cfg, "sla_espera_obj_min"))
        sla_descarga = _coerce_int(inbound_cfg_get(cfg, "sla_descarga_obj_min"))
        sla_total = _coerce_int(inbound_cfg_get(cfg, "sla_total_obj_min"))

        te = _coerce_int(metrics.get("tiempo_espera_min"))
        td = _coerce_int(metrics.get("tiempo_descarga_min"))
        tt = _coerce_int(metrics.get("tiempo_total_min"))

        if sla_espera and te is not None and te > sla_espera:
            mensajes.append(
                f"Recepción {recepcion.codigo}: tiempo de espera {te} min (SLA {sla_espera} min)."
            )

        if sla_descarga and td is not None and td > sla_descarga:
            mensajes.append(
                f"Recepción {recepcion.codigo}: tiempo de descarga {td} min (SLA {sla_descarga} min)."
            )

        if sla_total and tt is not None and tt > sla_total:
            mensajes.append(
                f"Recepción {recepcion.codigo}: tiempo total {tt} min (SLA {sla_total} min)."
            )

    # --- Incidencias críticas ---
    if bool(inbound_cfg_get(cfg, "habilitar_alertas_incidencias", False)):
        max_crit = _coerce_int(inbound_cfg_get(cfg, "max_incidencias_criticas_por_recepcion"))

        if max_crit is not None and hasattr(recepcion, "incidencias") and recepcion.incidencias:
            criticas = [
                i for i in recepcion.incidencias
                if (getattr(i, "criticidad", "") or "").lower() == "alta"
            ]
            if len(criticas) > max_crit:
                mensajes.append(f"Recepción {recepcion.codigo}: {len(criticas)} incidencias de criticidad ALTA.")

    # Crear alertas (dedupe por mensaje pendiente)
    for msg in mensajes:
        tipo = "inbound_sla"
        if _is_already_pending_alert(db, negocio.id, tipo, msg):
            continue

        alerta = Alerta(
            negocio_id=negocio.id,
            tipo=tipo,
            mensaje=msg,
            destino=None,
            estado="pendiente",
            origen="inbound",
        )
        db.add(alerta)
        created += 1

    if created:
        db.commit()
        logger.info(
            "[INBOUND][SLA] negocio_id=%s recepcion_id=%s codigo=%s alertas_creadas=%s",
            negocio.id, recepcion.id, recepcion.codigo, created,
        )

    return created
