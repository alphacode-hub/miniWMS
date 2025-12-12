# core/services/services_observability.py

from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.models import Negocio, Usuario, Producto, InboundRecepcion
from core.logging_config import logger
from core.plans import get_core_plan_config, get_inbound_plan_config


# ============================================================
#  HELPERS INTERNOS
# ============================================================

def _check_db_connection() -> Dict[str, Any]:
    """
    Verifica que la BD responde.
    Hace una consulta mínima a la tabla Negocio usando SessionLocal.
    Devuelve un dict con status e info.
    """
    db: Session = SessionLocal()
    try:
        total_negocios = db.query(Negocio).count()
        return {
            "status": "ok",
            "total_negocios": total_negocios,
        }
    except Exception as e:
        logger.exception("[HEALTH][DB] Error al chequear conexión BD")
        return {
            "status": "error",
            "error": str(e),
        }
    finally:
        db.close()


# ============================================================
#  HELPERS PÚBLICOS PARA ROUTES_HEALTH
# ============================================================

def check_db_connection(db: Session) -> bool:
    """
    Versión simplificada para usar en /health:
    retorna True/False según si la BD responde.

    Aunque recibimos un db (por Depends), reutilizamos la lógica
    centralizada de _check_db_connection para mantener un solo punto.
    """
    status = _check_db_connection()
    return status.get("status") == "ok"


def check_core_entities(db: Session) -> Dict[str, Any]:
    """
    Devuelve un snapshot muy simple de entidades core:
    - total_negocios
    - total_usuarios
    - total_productos
    - total_inbound
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


# ============================================================
#  HEALTH GLOBAL LIGERO
# ============================================================

def get_app_health(db: Session) -> Dict[str, Any]:
    """
    Salud global ligera de la app.
    Se puede usar en /health si quieres más detalle.
    """
    db_status = _check_db_connection()

    total_usuarios = db.query(Usuario).count()
    total_productos = db.query(Producto).count()
    total_inbound = db.query(InboundRecepcion).count()

    health = {
        "status": "ok" if db_status["status"] == "ok" else "degraded",
        "timestamp_utc": datetime.utcnow().isoformat(),
        "db": db_status,
        "core_wms": {
            "status": "ok",
            "total_usuarios": total_usuarios,
            "total_productos": total_productos,
        },
        "inbound": {
            "status": "ok",
            "total_recepciones": total_inbound,
        },
    }

    logger.info(
        "[HEALTH][GLOBAL] status=%s db_status=%s core_wms_users=%s inbound_recepciones=%s",
        health["status"],
        db_status["status"],
        total_usuarios,
        total_inbound,
    )
    return health


# ============================================================
#  SMOKE TEST DE DOMINIO
# ============================================================

def run_smoke_test(db: Session) -> Dict[str, Any]:
    """
    Smoke test de dominio: NO crea datos nuevos,
    pero verifica que las piezas principales respondan.

    Pensado para usarse en /health/smoke o /admin/smoke-test.
    """
    results: Dict[str, Any] = {
        "status": "ok",
        "checks": [],
    }

    def add_check(name: str, ok: bool, info: Any = None) -> None:
        results["checks"].append(
            {
                "name": name,
                "ok": ok,
                "info": info,
            }
        )
        if not ok:
            results["status"] = "degraded"

    # 1) DB básica
    db_status = _check_db_connection()
    add_check("db_connection", db_status["status"] == "ok", db_status)

    if db_status["status"] != "ok":
        return results

    # 2) Hay al menos un negocio
    negocios = db.query(Negocio).all()
    add_check("negocios_existentes", len(negocios) > 0, {"total": len(negocios)})

    if not negocios:
        return results

    negocio = negocios[0]

    # 3) Plan core WMS resolvible
    try:
        core_cfg = get_core_plan_config(negocio.plan_tipo or "demo")
        add_check("core_plan_config", True, {"plan_tipo": negocio.plan_tipo, "cfg": core_cfg})
    except Exception as e:
        add_check("core_plan_config", False, str(e))

    # 4) Plan inbound resolvible
    try:
        inbound_cfg = get_inbound_plan_config(negocio.plan_tipo or "demo")
        add_check("inbound_plan_config", True, {"plan_tipo": negocio.plan_tipo, "cfg": inbound_cfg})
    except Exception as e:
        add_check("inbound_plan_config", False, str(e))

    # 5) Actividad básica del negocio
    usuarios_negocio = db.query(Usuario).filter(Usuario.negocio_id == negocio.id).count()
    productos_negocio = db.query(Producto).filter(Producto.negocio_id == negocio.id).count()
    add_check(
        "negocio_actividad_basica",
        usuarios_negocio >= 0 and productos_negocio >= 0,
        {
            "negocio_id": negocio.id,
            "usuarios": usuarios_negocio,
            "productos": productos_negocio,
        },
    )

    # 6) Inbound para el negocio
    inbound_count = (
        db.query(InboundRecepcion)
        .filter(InboundRecepcion.negocio_id == negocio.id)
        .count()
    )
    add_check(
        "inbound_recepciones_negocio",
        inbound_count >= 0,
        {
            "negocio_id": negocio.id,
            "recepciones": inbound_count,
        },
    )

    logger.info("[SMOKE_TEST] status=%s checks=%s", results["status"], len(results["checks"]))
    return results
