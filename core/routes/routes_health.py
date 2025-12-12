# core/routes/routes_health.py

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import Negocio, Usuario
from core.services.services_observability import (
    check_db_connection,
    check_core_entities,
    run_smoke_test,
)
from core.security import require_roles_dep
from core.logging_config import logger

router = APIRouter(
    prefix="",
    tags=["health"],
)


@router.get("/health", response_class=JSONResponse)
async def health_root(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("superadmin")),  # 👈 solo superadmin
):
    """
    Health básico de la app (solo superadmin):
    - conexión a BD
    - conteo mínimo de negocios, usuarios, productos, inbound
    """
    try:
        db_ok = check_db_connection(db)
        entities = check_core_entities(db)

        payload = {
            "status": "ok" if db_ok else "degraded",
            "timestamp_utc": datetime.utcnow().isoformat(),
            "db": {"ok": db_ok},
            "core_entities": entities,
        }

        logger.info("[HEALTH] ok status=%s entities=%s", payload["status"], entities)
        return JSONResponse(status_code=200, content=payload)

    except Exception as e:
        logger.exception("[HEALTH] error global health")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "timestamp_utc": datetime.utcnow().isoformat(),
                "error": str(e),
            },
        )


@router.get("/health/smoke", response_class=JSONResponse)
async def health_smoke(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("superadmin")),  # 👈 también solo superadmin
):
    """
    Smoke test rápido de dominio:
    - ping interno a la BD
    - checks de negocio/planes/inbound usando run_smoke_test
    """
    start = datetime.utcnow()

    try:
        smoke = run_smoke_test(db)
        elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000

        payload = {
            "status": smoke["status"],
            "elapsed_ms": round(elapsed_ms, 2),
            "checks": smoke["checks"],
            "timestamp_utc": datetime.utcnow().isoformat(),
        }

        logger.info(
            "[HEALTH][SMOKE] status=%s elapsed_ms=%s checks=%s",
            payload["status"],
            payload["elapsed_ms"],
            len(smoke["checks"]),
        )

        return JSONResponse(status_code=200, content=payload)

    except Exception as e:
        elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
        logger.exception("[HEALTH][SMOKE] error elapsed_ms=%s", elapsed_ms)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "elapsed_ms": round(elapsed_ms, 2),
                "error": str(e),
                "timestamp_utc": datetime.utcnow().isoformat(),
            },
        )
