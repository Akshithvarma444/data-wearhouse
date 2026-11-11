"""Environment-based configuration.

Values are read from environment variables (optionally loaded from a ``.env``
file in the project root). Nothing secret is hard-coded: the only defaults are
non-sensitive ones such as directories, batch size and log level.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
SQL_DIR: Path = PROJECT_ROOT / "sql"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    """Validated, immutable runtime settings."""

    model_config = ConfigDict(frozen=True)

    database_url: str
    test_database_url: str | None = None
    log_level: str = "INFO"
    batch_size: int = Field(default=5000, gt=0)
    random_seed: int = 42
    source_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    reports_dir: Path = PROJECT_ROOT / "reports"
    logs_dir: Path = PROJECT_ROOT / "logs"
    source_system: str = "ecommerce_oltp"
    pipeline_name: str = "ecommerce_sales_dw"

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported LOG_LEVEL '{value}'")
        return level

    @field_validator("database_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL SQLAlchemy URL")
        return value


def _resolve_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def build_database_url() -> str:
    """Return DATABASE_URL, or assemble it from the individual DB_* variables."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("DB_HOST")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    port = os.getenv("DB_PORT", "5432")
    missing = [k for k, v in {"DB_HOST": host, "DB_NAME": name, "DB_USER": user,
                              "DB_PASSWORD": password}.items() if not v]
    if missing:
        raise ConfigurationError(
            "Database configuration missing. Set DATABASE_URL or "
            + ", ".join(missing) + " (copy .env.example to .env)."
        )
    return (f"postgresql+psycopg://{quote_plus(user or '')}:{quote_plus(password or '')}"
            f"@{host}:{port}/{name}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process (``.env`` values never override real env vars)."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        return Settings(
            database_url=build_database_url(),
            test_database_url=os.getenv("TEST_DATABASE_URL") or None,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            batch_size=int(os.getenv("BATCH_SIZE", "5000")),
            random_seed=int(os.getenv("RANDOM_SEED", "42")),
            source_data_dir=_resolve_path(os.getenv("SOURCE_DATA_DIR"), PROJECT_ROOT / "data" / "raw"),
            reports_dir=_resolve_path(os.getenv("REPORTS_DIR"), PROJECT_ROOT / "reports"),
            logs_dir=_resolve_path(os.getenv("LOGS_DIR"), PROJECT_ROOT / "logs"),
            source_system=os.getenv("SOURCE_SYSTEM", "ecommerce_oltp"),
            pipeline_name=os.getenv("PIPELINE_NAME", "ecommerce_sales_dw"),
        )
    except ValueError as exc:  # pydantic.ValidationError and int() errors
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
