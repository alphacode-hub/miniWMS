# routes_health.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from core.logging_config import logger  # 👈 añadimos logging

router = APIRouter(
    tags=["health"],
)


@router.get("/health", include_in_schema=False)
async def health():
    """
    Health check básico de la API.
    No toca la base de datos, solo indica que la app está viva.
    """
    return {
        "status": "ok",
        "env": settings.APP_ENV,
        "debug": settings.APP_DEBUG,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/health/db", include_in_schema=False)
async def health_db(db: Session = Depends(get_db)):
    """
    Health check de la base de datos.
    Ejecuta un SELECT 1 para verificar conectividad.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        # Logueamos el error real para monitoreo
        logger.error(f"[HEALTH_DB] Error en health check de BD: {e}")
        # Y devolvemos un mensaje genérico al cliente
        raise HTTPException(
            status_code=503,
            detail="Database not healthy",
        )

    return {
        "status": "ok",
        "database": "reachable",
        "timestamp": datetime.utcnow().isoformat(),
    }
