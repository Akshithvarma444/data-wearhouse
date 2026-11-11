"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import Engine

from app.config.settings import Settings, get_settings


def settings_dep() -> Settings:
    return get_settings()


def engine_dep(request: Request) -> Engine:
    """The process-wide SQLAlchemy engine created at startup (connection pooled)."""
    return request.app.state.engine
