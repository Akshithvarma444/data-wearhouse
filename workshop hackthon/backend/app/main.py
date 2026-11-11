"""FastAPI application entry point.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.api.routes import (analytics, dashboard, datasets, generator, history, pipeline, quality,
                            reports, system, warehouse)
from app.config.settings import API_VERSION, APP_NAME, get_settings
from app.database.connection import get_engine, init_db
from app.services.monitoring.logger import get_logger, setup_logging

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level, settings.logs_dir)
    for source in ("sample", "generated", "invalid", "uploads"):
        settings.dataset_dir(source).mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    app.state.engine = get_engine(settings.database_url)
    try:
        init_db(app.state.engine)  # idempotent: CREATE ... IF NOT EXISTS
        log.info("Database schema verified")
    except OperationalError as exc:
        log.error("Database not reachable at startup (%s); the API starts in degraded mode",
                  str(exc).splitlines()[0])
    yield
    app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=APP_NAME, version=API_VERSION, lifespan=lifespan,
                  description="ETL pipeline, dimensional warehouse and analytics API")
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_credentials=False, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"], expose_headers=["Content-Disposition"])

    @app.exception_handler(OperationalError)
    async def db_unavailable(_: Request, exc: OperationalError) -> JSONResponse:
        log.error("Database unavailable: %s", str(exc).splitlines()[0])
        return JSONResponse(status_code=503, content={
            "detail": "The database is not reachable. Check that PostgreSQL is running and "
                      "DATABASE_URL is correct."})

    @app.exception_handler(SQLAlchemyError)
    async def db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.exception("Database error", exc_info=exc)
        return JSONResponse(status_code=500, content={
            "detail": "A database error occurred. Has the schema been initialised "
                      "(python -m app.cli init-db)? See backend/logs/pipeline.log."})

    for module in (system, dashboard, datasets, generator, pipeline, quality, warehouse,
                   analytics, reports, history):
        app.include_router(module.router)
    return app


app = create_app()
