"""Dashboard summary endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from app.api.deps import engine_dep
from app.schemas.api import DashboardSummary
from app.services import reporting

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(engine: Engine = Depends(engine_dep)) -> dict:
    """Headline figures, latest runs, trend, categories, top products and quality."""
    return reporting.dashboard_summary(engine)
