"""Environment-based configuration for the backend.

Values come from environment variables, optionally loaded from ``backend/.env``.
Nothing secret is hard-coded; defaults exist only for non-sensitive values
(directories, batch size, log level, CORS origins).

Folder layout (``workshop hackthon/``)::

    backend/            this application (BACKEND_ROOT)
    other/sql/          SQL scripts executed by init_db           (SQL_DIR)
    other/datasets/     sample / generated / invalid / uploads    (DATASETS_DIR)
    other/reports/      reconciliation, quality, performance      (REPORTS_DIR)
"""

from __future__ import annotations

import os
import platform
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

BACKEND_ROOT: Path = Path(__file__).resolve().parents[2]
REPO_ROOT: Path = BACKEND_ROOT.parent
load_dotenv(BACKEND_ROOT / ".env", override=False)


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


SQL_DIR: Path = _path_from_env("SQL_DIR", REPO_ROOT / "other" / "sql")
DATASETS_DIR: Path = _path_from_env("DATASETS_DIR", REPO_ROOT / "other" / "datasets")
API_VERSION = "1.0.0"
APP_NAME = "E-Commerce Sales Data Warehouse API"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    """Validated, immutable runtime settings."""

    model_config = ConfigDict(frozen=True)

    database_url: str
    test_database_url: str | None = None
    environment: str = "development"
    log_level: str = "INFO"
    batch_size: int = Field(default=5000, gt=0)
    random_seed: int = 42
    datasets_dir: Path = DATASETS_DIR
    reports_dir: Path = REPO_ROOT / "other" / "reports"
    logs_dir: Path = BACKEND_ROOT / "logs"
    source_system: str = "ecommerce_oltp"
    pipeline_name: str = "ecommerce_sales_dw"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:4173")
    max_upload_mb: int = Field(default=200, gt=0)

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

    # Convenience locations ---------------------------------------------------
    @property
    def source_data_dir(self) -> Path:
        """Default pipeline input (the generated dataset)."""
        return self.datasets_dir / "generated"

    def dataset_dir(self, source: str) -> Path:
        """Directory of a named dataset: sample | generated | invalid | uploads."""
        if source not in {"sample", "generated", "invalid", "uploads"}:
            raise ValueError(f"unknown dataset source '{source}'")
        return self.datasets_dir / source

    @property
    def python_version(self) -> str:
        return platform.python_version()


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
            + ", ".join(missing) + " (copy backend/.env.example to backend/.env).")
    return (f"postgresql+psycopg://{quote_plus(user or '')}:{quote_plus(password or '')}"
            f"@{host}:{port}/{name}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process (real environment variables win over .env)."""
    try:
        origins = tuple(o.strip() for o in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",") if o.strip())
        return Settings(
            database_url=build_database_url(),
            test_database_url=os.getenv("TEST_DATABASE_URL") or None,
            environment=os.getenv("APP_ENV", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            batch_size=int(os.getenv("BATCH_SIZE", "5000")),
            random_seed=int(os.getenv("RANDOM_SEED", "42")),
            datasets_dir=DATASETS_DIR,
            reports_dir=_path_from_env("REPORTS_DIR", REPO_ROOT / "other" / "reports"),
            logs_dir=_path_from_env("LOGS_DIR", BACKEND_ROOT / "logs"),
            source_system=os.getenv("SOURCE_SYSTEM", "ecommerce_oltp"),
            pipeline_name=os.getenv("PIPELINE_NAME", "ecommerce_sales_dw"),
            cors_origins=origins,
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "200")),
        )
    except ValueError as exc:  # pydantic.ValidationError and int() errors
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
