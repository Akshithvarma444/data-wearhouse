"""Data quality endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Engine

from app.api.deps import engine_dep
from app.schemas.api import QualitySummary, RejectionPage
from app.services import reporting

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.get("/summary", response_model=QualitySummary)
def summary(engine: Engine = Depends(engine_dep)) -> dict:
    """Valid / rejected counts and rejection categories across all runs."""
    return reporting.quality_summary(engine)


@router.get("/rejections", response_model=RejectionPage)
def rejections(entity: Literal["customers", "products", "orders", "order_items"] | None = None,
               reason: str | None = Query(None, max_length=60, pattern=r"^[A-Z_]+$"),
               run_id: str | None = Query(None, max_length=64),
               limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
               engine: Engine = Depends(engine_dep)) -> dict:
    """Quarantined records with source file, line number, reasons and original values."""
    return reporting.rejections(engine, entity, reason, run_id, limit, offset)
