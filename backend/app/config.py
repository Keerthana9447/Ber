from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "BEREV ERP API"
    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    jwt_secret_key: SecretStr = Field(min_length=32)
    gateway_webhook_secret: SecretStr | None = None
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "berev-erp"
    access_token_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_days: int = Field(default=7, ge=1, le=90)
    login_max_attempts: int = Field(default=5, ge=1, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)
    password_reset_minutes: int = Field(default=30, ge=5, le=1440)
    notification_interval_seconds: int = Field(default=60, ge=10, le=3600)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True
    public_app_url: str = "http://localhost:5173"
    log_level: str = "INFO"
    expense_upload_dir: Path = PROJECT_ROOT / "data" / "expense-receipts"

    @property
    def smtp_configured(self) -> bool:
        credentials_complete = bool(self.smtp_username) == bool(self.smtp_password)
        return bool(self.smtp_host and self.smtp_from_email and credentials_complete)

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("database_url")
    @classmethod
    def require_postgresql_driver(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the postgresql+psycopg driver")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard Python logging level")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
