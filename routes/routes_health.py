# routes_health.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from database import get_db

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
        # Si falla, devolvemos 503 (Service Unavailable)
        raise HTTPException(
            status_code=503,
            detail=f"Database not healthy: {e}",
        )

    return {
        "status": "ok",
        "database": "reachable",
        "timestamp": datetime.utcnow().isoformat(),
    }
