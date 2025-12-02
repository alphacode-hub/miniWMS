import logging
from logging.config import dictConfig
from pathlib import Path

from config import settings


def setup_logging() -> None:
    # Carpeta de logs
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": str(log_dir / "app.log"),
                    "maxBytes": 5 * 1024 * 1024,  # 5 MB
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "root": {
                "level": "INFO" if not settings.DEBUG else "DEBUG",
                "handlers": ["file", "console"],
            },
        }
    )


logger = logging.getLogger("miniWMS")
