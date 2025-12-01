from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_ENV: str = "development"
    
    APP_DEBUG: bool = True # por defecto modo dev

    DATABASE_URL: str = "sqlite:///./miniWMS.db"

    
    APP_SECRET_KEY: str = "VeuoeH6L"

    SUPERADMIN_EMAIL: str = "root@superadmin.cl"
    SUPERADMIN_PASSWORD: str = "12345678"
    SUPERADMIN_BUSINESS_NAME: str = "Global"

    SESSION_TOKEN_BYTES: int = 32
    SESSION_EXPIRATION_MINUTES: int = 1440

    WHATSAPP_API_URL: str | None = None
    WHATSAPP_INSTANCE_ID: str | None = None
    WHATSAPP_TOKEN: str | None = None

    STOCK_ALERT_MIN_THRESHOLD: int = 5
    EXPIRATION_ALERT_DAYS: int = 30

    SESSION_COOKIE_NAME: str = "session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 4  # 4 horas
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_COOKIE_SECURE: bool = False  # True en producci�n con HTTPS

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
