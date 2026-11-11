"""Pipeline execution, live status and history endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.api.deps import engine_dep, settings_dep
from app.config.settings import Settings
from app.schemas.api import PipelineRun, PipelineRunAccepted, PipelineRunRequest, PipelineStatus
from app.services import reporting
from app.services.datasets import inspect_file
from app.services.run_tracker import STAGES, PipelineBusyError, tracker
from app.services.validation.schemas import ENTITY_LOAD_ORDER, ENTITY_SPECS

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run", response_model=PipelineRunAccepted, status_code=202)
def run(body: PipelineRunRequest, engine: Engine = Depends(engine_dep),
        settings: Settings = Depends(settings_dep)) -> PipelineRunAccepted:
    """Start the ETL pipeline in the background. Poll /status/{run_id} for progress."""
    directory = settings.dataset_dir(body.source)
    missing = [ENTITY_SPECS[e].file_name for e in ENTITY_LOAD_ORDER
               if not inspect_file(e, directory / ENTITY_SPECS[e].file_name)["exists"]]
    if missing:
        raise HTTPException(422, f"The '{body.source}' dataset is incomplete; missing "
                                 f"{', '.join(missing)}. Generate or upload the files first.")
    try:
        run_id = tracker.start(engine, settings, directory, body.source, body.strict_schema)
    except PipelineBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    return PipelineRunAccepted(run_id=run_id, status="RUNNING", source=body.source)


@router.get("/status/{run_id}", response_model=PipelineStatus)
def status(run_id: str, engine: Engine = Depends(engine_dep)) -> dict:
    """Real progress of a run: stage states from the live tracker (or from the stored
    stage metrics for runs that finished earlier / were started from the CLI)."""
    if len(run_id) > 64:
        raise HTTPException(422, "run_id is too long")
    row = reporting.pipeline_run(engine, run_id)
    live = tracker.get(run_id)
    if row is None and live is None:
        raise HTTPException(404, f"run '{run_id}' not found")
    if row is None:  # tracked but its run row was never written (e.g. database down)
        assert live is not None
        row = {"run_id": run_id, "status": "FAILED" if live.finished else "RUNNING",
               "source": live.source, "start_time": None, "end_time": None,
               "duration_seconds": None, "error_message": live.error}

    if live is not None:
        stage_status, seconds = live.stage_status, live.stage_seconds
        current = live.current_stage
    else:
        seconds = reporting.run_stage_seconds(engine, run_id)
        stage_status = {name: "done" for name in seconds}
        current = None
    if row["status"] == "FAILED" and current:
        stage_status[current] = "failed"

    stages = [{"name": n, "label": label, "status": stage_status.get(n, "pending"),
               "seconds": seconds.get(n)} for n, label in STAGES]
    done = sum(1 for s in stages if s["status"] == "done")
    recon = live.reconciliation_passed if live else (
        None if row["status"] not in ("SUCCESS", "RECONCILIATION_FAILED")
        else row["status"] == "SUCCESS")
    if row.get("start_time") is None:
        row["start_time"] = datetime.now(timezone.utc)
    return {**row, "current_stage": current if row["status"] == "RUNNING" else None,
            "stages": stages, "progress_percent": round(100 * done / len(stages)),
            "reconciliation_passed": recon}


@router.get("/history", response_model=list[PipelineRun])
def history(limit: int = Query(50, ge=1, le=1000), engine: Engine = Depends(engine_dep)) -> list:
    """Most recent pipeline runs from ops.pipeline_runs."""
    return reporting.pipeline_runs(engine, limit)
