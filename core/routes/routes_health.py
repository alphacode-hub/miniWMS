# core/routes/routes_health.py
"""
Health & Observability routes – ORBION (SaaS enterprise)

✔ Endpoints SOLO para superadmin
✔ Health básico (rápido y seguro)
✔ Smoke test de dominio
✔ Respuestas JSON consistentes
✔ Logs estructurados
✔ Sin exponer datos sensibles
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.services.services_observability import (
    check_db_connection,
    check_core_entities,
    run_smoke_test,
)
from core.security import require_roles_dep
from core.logging_config import logger


# ============================
# ROUTER
# ============================

router = APIRouter(
    tags=["health"],
)


# ============================
# HEALTH BÁSICO
# ============================

@router.get("/health", response_class=JSONResponse)
async def health_root(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("superadmin")),  # 🔒 solo superadmin
):
    """
    Health básico del sistema.

    Pensado para:
    - verificación manual
    - monitoreo interno
    - panel superadmin
    """
    start = datetime.utcnow()

    try:
        db_ok = check_db_connection(db)
        entities = check_core_entities(db)

        payload = {
            "status": "ok" if db_ok else "degraded",
            "timestamp_utc": datetime.utcnow().isoformat(),
            "elapsed_ms": round((datetime.utcnow() - start).total_seconds() * 1000, 2),
            "db": {
                "ok": db_ok,
            },
            "core_entities": entities,
        }

        logger.info(
            "[HEALTH] status=%s db_ok=%s entities=%s",
            payload["status"],
            db_ok,
            entities,
        )

        return JSONResponse(status_code=200, content=payload)

    except Exception as exc:
        logger.exception("[HEALTH] error")

        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "timestamp_utc": datetime.utcnow().isoformat(),
                "error": str(exc),
            },
        )


# ============================
# SMOKE TEST
# ============================

@router.get("/health/smoke", response_class=JSONResponse)
async def health_smoke(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("superadmin")),  # 🔒 solo superadmin
):
    """
    Smoke test de dominio (rápido).

    ✔ No crea datos
    ✔ Valida planes
    ✔ Valida entidades core
    ✔ Útil antes de deploy
    """
    start = datetime.utcnow()

    try:
        smoke = run_smoke_test(db)
        elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000

        payload = {
            "status": smoke.get("status", "unknown"),
            "elapsed_ms": round(elapsed_ms, 2),
            "checks": smoke.get("checks", []),
            "timestamp_utc": datetime.utcnow().isoformat(),
        }

        logger.info(
            "[HEALTH][SMOKE] status=%s elapsed_ms=%s checks=%s",
            payload["status"],
            payload["elapsed_ms"],
            len(payload["checks"]),
        )

        return JSONResponse(status_code=200, content=payload)

    except Exception as exc:
        elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
        logger.exception("[HEALTH][SMOKE] error")

        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "elapsed_ms": round(elapsed_ms, 2),
                "error": str(exc),
                "timestamp_utc": datetime.utcnow().isoformat(),
            },
        )
