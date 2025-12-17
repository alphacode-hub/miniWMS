# modules/inbound_orbion/routes/routes_inbound_core.py
"""
Compat Layer – Inbound ORBION (baseline)

Mantiene rutas legacy SIN lógica de negocio.
Toda la source of truth vive en routes_inbound_recepciones.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from core.database import get_db
from core.models import InboundRecepcion
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from .inbound_common import inbound_roles_dep

router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================
#   LEGACY ROOT
# ============================


@router.get("/", include_in_schema=False)
async def inbound_root_legacy(
    request: Request,
    _user=Depends(inbound_roles_dep()),
):
    return RedirectResponse(url="/inbound/recepciones", status_code=302)


# ============================
#   LEGACY NUEVO
# ============================

@router.get("/nuevo", include_in_schema=False)
async def inbound_nuevo_legacy(
    request: Request,
    _user=Depends(inbound_roles_dep()),
):
    return RedirectResponse(url="/inbound/recepciones/nueva", status_code=302)


# ============================
#   HEALTH (opcional)
# ============================

@router.get("/health", response_class=JSONResponse)
async def inbound_health(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    try:
        total_recepciones = (
            db.query(func.count(InboundRecepcion.id))
            .filter(InboundRecepcion.negocio_id == negocio_id)
            .scalar()
        ) or 0

        log_inbound_event(
            "health_ok",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            total_recepciones=total_recepciones,
        )

        return {
            "status": "ok",
            "negocio_id": negocio_id,
            "total_recepciones": total_recepciones,
            "timestamp_utc": _utcnow().isoformat(),
        }

    except Exception as e:
        log_inbound_error(
            "health_error",
            negocio_id=negocio_id,
            error=e,
            user_email=user.get("email"),
        )

        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "negocio_id": negocio_id,
                "error": str(e),
                "timestamp_utc": _utcnow().isoformat(),
            },
        )
