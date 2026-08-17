from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "informe-finca-backend"
    debug: bool = False
    environment: str = "local"

    public_base_url: str = "http://localhost:8000"
    site_base_url: str = "http://localhost:4321"
    # Comma-separated instead of a JSON list: .env files are edited by humans.
    cors_origins: str = "http://localhost:4321"

    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "informefinca"
    postgres_host: str = "db"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    report_price_eur: int = 39

    reports_dir: Path = Path("/data/reports")

    http_user_agent: str = "informefinca.es/1.0 (+https://informefinca.es)"
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    ndvi_years: int = 8

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    mail_from: str = "contacto@informefinca.es"
    mail_from_name: str = "informefinca.es"

    preview_rate_limit: int = 20
    parcel_cache_days: int = 30

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ndvi_enabled(self) -> bool:
        return bool(self.cdse_client_id and self.cdse_client_secret)

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
