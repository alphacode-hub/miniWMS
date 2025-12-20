# core/services/services_observability.py
"""
Observability / Health – ORBION (SaaS enterprise)

✔ Health check DB (rápido y confiable)
✔ Snapshot de entidades core
✔ Health global (ok/degraded)
✔ Smoke test de dominio (sin crear datos)
✔ Logs consistentes y sin duplicar sesiones

✅ Baseline SaaS modular:
- Fuente única: Negocio.entitlements (services_entitlements)
- Legacy plan_tipo: solo fallback interno (services_entitlements lo resuelve)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Negocio, Usuario, Producto
from core.models.inbound import InboundRecepcion
from core.models.time import utcnow

from core.services.services_entitlements import (
    resolve_entitlements,
    get_entitlements_snapshot,
)


# ============================================================
#  HELPERS INTERNOS
# ============================================================

def _db_select_one(db: Session) -> bool:
    """
    Ping mínimo a DB usando la sesión inyectada.
    """
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("[HEALTH][DB] Error en SELECT 1")
        return False


def _safe_count(fn, label: str) -> int:
    """
    Ejecuta un count defensivo. Si falla, retorna -1 y loggea.
    """
    try:
        return int(fn())
    except Exception as exc:
        logger.exception("[HEALTH][COUNT] Error en %s: %s", label, exc)
        return -1


def _check_db_connection(db: Session) -> dict[str, Any]:
    """
    Retorna status detallado.
    """
    ok = _db_select_one(db)
    if not ok:
        return {"status": "error"}

    total_negocios = _safe_count(lambda: db.query(Negocio).count(), "Negocio.count")
    if total_negocios < 0:
        return {"status": "error", "error": "No se pudo contar negocios"}

    return {
        "status": "ok",
        "total_negocios": total_negocios,
    }


# ============================================================
#  API PÚBLICA PARA ROUTES_HEALTH
# ============================================================

def check_db_connection(db: Session) -> bool:
    """
    Versión booleana para /health.
    """
    return _check_db_connection(db).get("status") == "ok"


def check_core_entities(db: Session) -> dict[str, Any]:
    """
    Snapshot de entidades principales.
    """
    total_negocios = _safe_count(lambda: db.query(Negocio).count(), "Negocio.count")
    total_usuarios = _safe_count(lambda: db.query(Usuario).count(), "Usuario.count")
    total_productos = _safe_count(lambda: db.query(Producto).count(), "Producto.count")
    total_inbound = _safe_count(lambda: db.query(InboundRecepcion).count(), "InboundRecepcion.count")

    entities = {
        "negocios": total_negocios,
        "usuarios": total_usuarios,
        "productos": total_productos,
        "inbound_recepciones": total_inbound,
    }

    logger.info(
        "[HEALTH][ENTITIES] negocios=%s usuarios=%s productos=%s inbound=%s",
        total_negocios,
        total_usuarios,
        total_productos,
        total_inbound,
    )
    return entities


def get_app_health(db: Session) -> dict[str, Any]:
    """
    Health global: ok / degraded.
    """
    db_status = _check_db_connection(db)

    health = {
        "timestamp_utc": utcnow().isoformat(),
        "db": db_status,
        "status": "ok" if db_status.get("status") == "ok" else "degraded",
    }

    # Solo añadimos métricas si DB OK (evita explosiones en degraded)
    if health["status"] == "ok":
        total_usuarios = _safe_count(lambda: db.query(Usuario).count(), "Usuario.count")
        total_productos = _safe_count(lambda: db.query(Producto).count(), "Producto.count")
        total_inbound = _safe_count(lambda: db.query(InboundRecepcion).count(), "InboundRecepcion.count")

        health["core_wms"] = {
            "status": "ok" if total_usuarios >= 0 and total_productos >= 0 else "degraded",
            "total_usuarios": total_usuarios,
            "total_productos": total_productos,
        }
        health["inbound"] = {
            "status": "ok" if total_inbound >= 0 else "degraded",
            "total_recepciones": total_inbound,
        }

        logger.info(
            "[HEALTH][GLOBAL] status=%s negocios=%s usuarios=%s inbound=%s",
            health["status"],
            db_status.get("total_negocios"),
            total_usuarios,
            total_inbound,
        )
    else:
        logger.warning("[HEALTH][GLOBAL] status=degraded db_status=%s", db_status.get("status"))

    return health


# ============================================================
#  SMOKE TEST DE DOMINIO (SaaS modular)
# ============================================================

def run_smoke_test(db: Session) -> dict[str, Any]:
    """
    Smoke test de dominio: no crea datos.

    ✅ Validaciones SaaS:
    - DB OK
    - Hay negocios
    - Entitlements resolvibles (incluye fallback legacy interno)
    - Snapshot del negocio (Hub) resolvible
    - Actividad mínima por negocio
    """
    results: dict[str, Any] = {"status": "ok", "checks": []}

    def add_check(name: str, ok: bool, info: Any = None) -> None:
        results["checks"].append({"name": name, "ok": ok, "info": info})
        if not ok:
            results["status"] = "degraded"

    # 1) DB
    db_status = _check_db_connection(db)
    add_check("db_connection", db_status.get("status") == "ok", db_status)
    if db_status.get("status") != "ok":
        return results

    # 2) negocios (sin cargar todos)
    total_negocios = _safe_count(lambda: db.query(Negocio).count(), "Negocio.count")
    add_check("negocios_existentes", total_negocios > 0, {"total": total_negocios})
    if total_negocios <= 0:
        return results

    negocio = db.query(Negocio).order_by(Negocio.id.asc()).first()
    if not negocio:
        add_check("negocio_sample", False, "No se pudo obtener negocio sample")
        return results
    add_check("negocio_sample", True, {"negocio_id": negocio.id})

    # 3) entitlements (fuente única, legacy solo fallback interno)
    try:
        ent = resolve_entitlements(negocio)

        ok_contract = (
            isinstance(ent, dict)
            and isinstance(ent.get("modules"), dict)
            and isinstance(ent.get("segment"), str)
        )

        add_check(
            "entitlements_resolve",
            ok_contract,
            {
                "negocio_id": negocio.id,
                "plan_tipo_legacy": getattr(negocio, "plan_tipo", None),
                "segment": ent.get("segment"),
                "modules": list((ent.get("modules") or {}).keys()),
            },
        )
        if not ok_contract:
            return results
    except Exception as exc:
        add_check("entitlements_resolve", False, str(exc))
        return results

    # 4) snapshot HUB (entitlements + overlay + usage/limits)
    try:
        snap = get_entitlements_snapshot(db, negocio.id)
        modules = snap.get("modules", {}) or {}
        add_check(
            "entitlements_snapshot",
            isinstance(snap, dict) and "negocio" in snap and isinstance(modules, dict),
            {
                "negocio_id": negocio.id,
                "modules": list(modules.keys()),
            },
        )
    except Exception as exc:
        add_check("entitlements_snapshot", False, str(exc))

    # 5) actividad negocio
    try:
        usuarios_negocio = db.query(Usuario).filter(Usuario.negocio_id == negocio.id).count()
        productos_negocio = db.query(Producto).filter(Producto.negocio_id == negocio.id).count()
        add_check(
            "negocio_actividad_basica",
            usuarios_negocio >= 0 and productos_negocio >= 0,
            {"negocio_id": negocio.id, "usuarios": usuarios_negocio, "productos": productos_negocio},
        )
    except Exception as exc:
        add_check("negocio_actividad_basica", False, str(exc))

    # 6) inbound negocio
    try:
        inbound_count = db.query(InboundRecepcion).filter(InboundRecepcion.negocio_id == negocio.id).count()
        add_check(
            "inbound_recepciones_negocio",
            inbound_count >= 0,
            {"negocio_id": negocio.id, "recepciones": inbound_count},
        )
    except Exception as exc:
        add_check("inbound_recepciones_negocio", False, str(exc))

    logger.info("[SMOKE_TEST] status=%s checks=%s", results["status"], len(results["checks"]))
    return results
