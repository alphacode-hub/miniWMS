# core/config.py
"""
Configuración central de ORBION.

✔ Compatible con SaaS enterprise
✔ Multi-entorno (development / staging / production)
✔ Pydantic Settings v2
✔ Ajustes automáticos y seguros por entorno
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ============================
    #   Pydantic settings
    # ============================
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # ============================
    #   ENTORNO
    # ============================
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_DEBUG: bool = True  # Se fuerza automáticamente según entorno

    # ============================
    #   BASE DE DATOS
    # ============================
    DATABASE_URL: str = "sqlite:///./miniWMS.db"

    # ============================
    #   SEGURIDAD / SESIONES
    # ============================
    APP_SECRET_KEY: str

    SUPERADMIN_EMAIL: str = "super@orbion.cl"
    SUPERADMIN_PASSWORD: str = "VeuoeH6L"
    SUPERADMIN_BUSINESS_NAME: str = "Orbion"
    SUPERADMIN_DISPLAY_NAME: str = "Superadmin"


    SESSION_TOKEN_BYTES: int = 32
    SESSION_EXPIRATION_MINUTES: int = 480  # 8 horas

    SESSION_COOKIE_NAME: str = "session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 8  # 8 horas
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    SESSION_COOKIE_SECURE: bool = False  # Forzado en production

    # ============================
    #   WHATSAPP / NOTIFICACIONES
    # ============================
    WHATSAPP_API_URL: str | None = None
    WHATSAPP_INSTANCE_ID: str | None = None
    WHATSAPP_TOKEN: str | None = None

    # ============================
    #   ALERTAS / REGLAS DE NEGOCIO
    # ============================
    STOCK_ALERT_MIN_THRESHOLD: int = 5
    EXPIRATION_ALERT_DAYS: int = 30

    # ============================
    #   POST INIT (ENTERPRISE)
    # ============================
    def model_post_init(self, __context) -> None:
        """
        Ajustes automáticos por entorno.
        """
        env = (self.APP_ENV or "development").lower()

        if env == "production":
            # Seguridad estricta en producción
            object.__setattr__(self, "APP_DEBUG", False)
            object.__setattr__(self, "SESSION_COOKIE_SECURE", True)

        elif env == "staging":
            # Staging sin debug, cookies según ENV
            object.__setattr__(self, "APP_DEBUG", False)

        else:
            # Development
            object.__setattr__(self, "APP_DEBUG", True)


# ============================
#   INSTANCIA GLOBAL
# ============================
settings = Settings()
