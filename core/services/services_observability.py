# core/services/services_observability.py
"""
Observability / Health – ORBION (SaaS enterprise)

✔ Health check DB (rápido y confiable)
✔ Snapshot de entidades core
✔ Health global (ok/degraded)
✔ Smoke test de dominio (sin crear datos)
✔ Logs consistentes y sin duplicar sesiones
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Negocio, Usuario, Producto
from core.models.inbound import InboundRecepcion
from core.plans import get_core_plan_config, get_inbound_plan_config


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
    except Exception as exc:
        logger.exception("[HEALTH][DB] Error en SELECT 1")
        return False


def _check_db_connection(db: Session) -> dict[str, Any]:
    """
    Retorna status detallado.
    """
    ok = _db_select_one(db)
    if not ok:
        return {"status": "error"}

    try:
        total_negocios = db.query(Negocio).count()
        return {
            "status": "ok",
            "total_negocios": total_negocios,
        }
    except Exception as exc:
        logger.exception("[HEALTH][DB] Error consultando Negocio")
        return {"status": "error", "error": str(exc)}


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
    total_negocios = db.query(Negocio).count()
    total_usuarios = db.query(Usuario).count()
    total_productos = db.query(Producto).count()
    total_inbound = db.query(InboundRecepcion).count()

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
        "timestamp_utc": datetime.utcnow().isoformat(),
        "db": db_status,
        "status": "ok" if db_status.get("status") == "ok" else "degraded",
    }

    # Solo añadimos métricas si DB OK (evita explosiones en degraded)
    if health["status"] == "ok":
        total_usuarios = db.query(Usuario).count()
        total_productos = db.query(Producto).count()
        total_inbound = db.query(InboundRecepcion).count()

        health["core_wms"] = {
            "status": "ok",
            "total_usuarios": total_usuarios,
            "total_productos": total_productos,
        }
        health["inbound"] = {
            "status": "ok",
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
#  SMOKE TEST DE DOMINIO
# ============================================================

def run_smoke_test(db: Session) -> dict[str, Any]:
    """
    Smoke test de dominio: no crea datos.
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

    # 2) negocios
    negocios = db.query(Negocio).all()
    add_check("negocios_existentes", len(negocios) > 0, {"total": len(negocios)})
    if not negocios:
        return results

    negocio = negocios[0]

    # 3) plan core
    try:
        core_cfg = get_core_plan_config(negocio.plan_tipo or "demo")
        add_check("core_plan_config", True, {"plan_tipo": negocio.plan_tipo, "cfg": core_cfg})
    except Exception as exc:
        add_check("core_plan_config", False, str(exc))

    # 4) plan inbound
    try:
        inbound_cfg = get_inbound_plan_config(negocio.plan_tipo or "demo")
        add_check("inbound_plan_config", True, {"plan_tipo": negocio.plan_tipo, "cfg": inbound_cfg})
    except Exception as exc:
        add_check("inbound_plan_config", False, str(exc))

    # 5) actividad negocio
    usuarios_negocio = db.query(Usuario).filter(Usuario.negocio_id == negocio.id).count()
    productos_negocio = db.query(Producto).filter(Producto.negocio_id == negocio.id).count()
    add_check(
        "negocio_actividad_basica",
        usuarios_negocio >= 0 and productos_negocio >= 0,
        {"negocio_id": negocio.id, "usuarios": usuarios_negocio, "productos": productos_negocio},
    )

    # 6) inbound negocio
    inbound_count = db.query(InboundRecepcion).filter(InboundRecepcion.negocio_id == negocio.id).count()
    add_check(
        "inbound_recepciones_negocio",
        inbound_count >= 0,
        {"negocio_id": negocio.id, "recepciones": inbound_count},
    )

    logger.info("[SMOKE_TEST] status=%s checks=%s", results["status"], len(results["checks"]))
    return results
