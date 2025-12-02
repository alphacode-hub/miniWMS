# config.py
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuración central de la app.
    Lee variables desde .env y aplica ajustes según APP_ENV.
    """

    # Pydantic Settings v2: configuración del archivo .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # -----------------------------
    # ENTORNO
    # -----------------------------
    APP_ENV: Literal["development", "staging", "production"] = "development"

    # Flag base (se ajusta automáticamente según APP_ENV)
    APP_DEBUG: bool = True  # en production lo forzamos a False

    # -----------------------------
    # BASE DE DATOS
    # -----------------------------
    # En desarrollo, por defecto SQLite local.
    # En staging/producción, se recomienda sobreescribir por ENV:
    #   DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/mini_wms
    DATABASE_URL: str = "sqlite:///./miniWMS.db"

    # -----------------------------
    # SEGURIDAD / SESIONES
    # -----------------------------
    APP_SECRET_KEY: str = "VeuoeH6L"  # ⚠️ en producción CAMBIAR en el .env

    SUPERADMIN_EMAIL: str = "root@superadmin.cl"
    SUPERADMIN_PASSWORD: str = "12345678"  # ⚠️ idem, solo para dev/demo
    SUPERADMIN_BUSINESS_NAME: str = "Global"

    SESSION_TOKEN_BYTES: int = 32
    SESSION_EXPIRATION_MINUTES: int = 1440  # 24h

    SESSION_COOKIE_NAME: str = "session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 4  # 4 horas
    SESSION_COOKIE_SAMESITE: str = "lax"
    # En dev False, en production lo forzamos a True automáticamente
    SESSION_COOKIE_SECURE: bool = False

    # -----------------------------
    # WHATSAPP / NOTIFICACIONES
    # -----------------------------
    WHATSAPP_API_URL: str | None = None
    WHATSAPP_INSTANCE_ID: str | None = None
    WHATSAPP_TOKEN: str | None = None

    # -----------------------------
    # REGLAS DE ALERTAS
    # -----------------------------
    STOCK_ALERT_MIN_THRESHOLD: int = 5
    EXPIRATION_ALERT_DAYS: int = 30

    # -----------------------------
    # AJUSTES AUTOMÁTICOS POR ENTORNO
    # -----------------------------
    def __init__(self, **data):
        super().__init__(**data)

        env = (self.APP_ENV or "development").lower()

        # En production:
        # - Debug SIEMPRE desactivado
        # - Cookies marcadas como "secure" (solo envía por HTTPS)
        if env == "production":
            object.__setattr__(self, "APP_DEBUG", False)
            object.__setattr__(self, "SESSION_COOKIE_SECURE", True)

        # En staging:
        # - Debug también desactivado, pero cookies secure lo decides por ENV
        elif env == "staging":
            object.__setattr__(self, "APP_DEBUG", False)


# Instancia global que usarás en el resto del proyecto
settings = Settings()
