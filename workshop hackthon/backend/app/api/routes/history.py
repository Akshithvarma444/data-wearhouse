"""Pipeline history endpoints (alias of /api/pipeline/history plus single-run lookup)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.api.deps import engine_dep
from app.schemas.api import PipelineRun
from app.services import reporting

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=list[PipelineRun])
def runs(limit: int = Query(50, ge=1, le=1000), engine: Engine = Depends(engine_dep)) -> list:
    """Most recent pipeline runs."""
    return reporting.pipeline_runs(engine, limit)


@router.get("/{run_id}", response_model=PipelineRun)
def run(run_id: str, engine: Engine = Depends(engine_dep)) -> dict:
    """One pipeline run."""
    row = reporting.pipeline_run(engine, run_id[:64])
    if row is None:
        raise HTTPException(404, f"run '{run_id}' not found")
    return row
