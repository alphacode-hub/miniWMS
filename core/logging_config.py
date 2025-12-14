# core/logging_config.py
"""
Logging centralizado – ORBION (SaaS enterprise)

✔ Logs a archivo + consola
✔ Rotación segura
✔ Niveles por entorno
✔ Formato consistente (auditoría + debugging)
✔ Preparado para crecer a ELK / Azure Monitor
"""

from __future__ import annotations

import logging
from logging.config import dictConfig
from pathlib import Path

from core.config import settings


# ============================
#   PATHS
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "orbion.log"


# ============================
#   LOGGING SETUP
# ============================

def setup_logging() -> None:
    """
    Configura logging global de la aplicación.
    """
    log_level = "DEBUG" if settings.APP_DEBUG else "INFO"

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,

            # ----------------------------
            # FORMATTERS
            # ----------------------------
            "formatters": {
                "default": {
                    "format": (
                        "%(asctime)s | %(levelname)s | "
                        "%(name)s | %(message)s"
                    ),
                },
                "verbose": {
                    "format": (
                        "%(asctime)s | %(levelname)s | "
                        "%(name)s | %(module)s:%(lineno)d | %(message)s"
                    ),
                },
            },

            # ----------------------------
            # HANDLERS
            # ----------------------------
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": log_level,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "verbose",
                    "filename": str(LOG_FILE),
                    "maxBytes": 5 * 1024 * 1024,  # 5 MB
                    "backupCount": 10,
                    "encoding": "utf-8",
                    "level": log_level,
                },
            },

            # ----------------------------
            # ROOT LOGGER
            # ----------------------------
            "root": {
                "level": log_level,
                "handlers": ["console", "file"],
            },

            # ----------------------------
            # LIBRARIES CONTROL
            # ----------------------------
            "loggers": {
                "uvicorn": {"level": "INFO"},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"level": "WARNING"},
                "sqlalchemy.engine": {
                    "level": "INFO" if settings.APP_DEBUG else "WARNING"
                },
            },
        }
    )


# ============================
#   LOGGER GLOBAL
# ============================

logger = logging.getLogger("orbion")
