"""Typed application settings loaded from environment or a local .env file."""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_env: str = "development"
    app_timezone: str = "America/Argentina/Buenos_Aires"
    database_host: str = "localhost"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "football_analytics_pro"
    database_user: str = "football_app"
    database_password: SecretStr = SecretStr("")
    football_data_token: SecretStr = SecretStr("")
    sync_enabled: bool = True
    sync_time: str = "08:00"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("sync_time")
    @classmethod
    def validate_sync_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("sync_time debe usar formato HH:MM")
        hour, minute = map(int, parts)
        if hour > 23 or minute > 59:
            raise ValueError("sync_time debe ser una hora válida de 24 horas")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("database_host")
    @classmethod
    def require_local_database(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("La base de datos debe estar en el equipo local")
        return normalized

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Zona horaria desconocida: {value}") from exc
        return value

    @property
    def database_url(self) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value() or None,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
