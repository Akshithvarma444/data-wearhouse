"""Report endpoints and CSV/JSON export."""

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import Engine

from app.api.deps import engine_dep, settings_dep
from app.config.settings import Settings
from app.schemas.api import PerformanceReport, ReconciliationReport
from app.services import reporting

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/reconciliation", response_model=ReconciliationReport)
def reconciliation(run_id: str | None = Query(None, max_length=64),
                   engine: Engine = Depends(engine_dep),
                   settings: Settings = Depends(settings_dep)) -> dict:
    """Source-vs-warehouse checks for a run (default: latest completed run)."""
    return reporting.reconciliation_report(engine, run_id, settings.source_system)


@router.get("/performance", response_model=PerformanceReport)
def performance(engine: Engine = Depends(engine_dep),
                settings: Settings = Depends(settings_dep)) -> dict:
    """Benchmark results (from the CLI benchmark) plus timings of recent runs."""
    return reporting.performance_report(engine, settings.reports_dir)


@router.get("/export")
def export(report: Literal["reconciliation", "quality", "rejections", "performance", "history"],
           format: Literal["csv", "json"] = "csv",
           engine: Engine = Depends(engine_dep),
           settings: Settings = Depends(settings_dep)) -> Response:
    """Download a report as CSV or JSON."""
    rows = reporting.export_rows(engine, report, settings.reports_dir, settings.source_system)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{report}_report_{stamp}.{format}"
    if format == "csv":
        body, media = reporting.to_csv(rows), "text/csv; charset=utf-8"
    else:
        body = json.dumps([{k: reporting.plain(v) for k, v in r.items()} for r in rows], indent=2)
        media = "application/json"
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
