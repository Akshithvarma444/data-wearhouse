"""Health and configuration endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import Engine, make_url

from app.api.deps import engine_dep, settings_dep
from app.config.settings import API_VERSION, APP_NAME, Settings
from app.database.connection import check_database
from app.schemas.api import AppConfig, Health

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=Health)
def health(engine: Engine = Depends(engine_dep)) -> Health:
    """API liveness plus database connectivity."""
    db = check_database(engine)
    return Health(status="ok" if db["connected"] else "degraded", api_version=API_VERSION,
                  time=datetime.now(timezone.utc), database=db)


@router.get("/settings/config", response_model=AppConfig)
def config(settings: Settings = Depends(settings_dep)) -> AppConfig:
    """Non-secret runtime configuration (the password is never returned)."""
    url = make_url(settings.database_url)
    return AppConfig(
        app_name=APP_NAME, api_version=API_VERSION, environment=settings.environment,
        python_version=settings.python_version, database_name=url.database or "",
        database_host=f"{url.host}:{url.port or 5432}", batch_size=settings.batch_size,
        random_seed=settings.random_seed, source_system=settings.source_system,
        datasets_dir=str(settings.datasets_dir), reports_dir=str(settings.reports_dir),
        cors_origins=list(settings.cors_origins))
