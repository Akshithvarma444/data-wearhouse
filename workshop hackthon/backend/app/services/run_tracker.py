"""Background pipeline execution and live stage tracking for the API.

``POST /api/pipeline/run`` starts the real pipeline (``services.pipeline.run_pipeline``)
in a worker thread. The pipeline's ``StageTimer`` notifies this tracker when each
stage starts and finishes, so ``GET /api/pipeline/status/{run_id}`` reports the
actual current stage. The tracker is in-memory; after a restart (or for runs
started from the CLI) stage timings are rebuilt from ``ops.pipeline_metrics``.
Only one pipeline run is started at a time by the API.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from app.config.settings import Settings
from app.services.monitoring.logger import get_logger
from app.services.pipeline import generate_run_id, run_pipeline

log = get_logger("run_tracker")

# Pipeline stages in execution order (load_warehouse is only a container stage).
STAGES: list[tuple[str, str]] = [
    ("extract", "Extraction"),
    ("validate", "Validation (schema + business rules)"),
    ("land_raw_and_quarantine", "Raw landing & quarantine"),
    ("transform", "Transformation (dimensions & typing)"),
    ("load_source", "Load source tables"),
    ("load_dimensions", "Load dimensions (SCD Type 2)"),
    ("transform_facts", "Build fact rows"),
    ("load_dim_date", "Load date dimension"),
    ("resolve_keys", "Resolve surrogate keys"),
    ("load_facts", "Load fact table"),
    ("reconcile", "Reconciliation"),
]
STAGE_NAMES = {name for name, _ in STAGES}


class PipelineBusyError(RuntimeError):
    """A pipeline run started through the API is still running."""


@dataclass
class TrackedRun:
    run_id: str
    source: str
    stage_status: dict[str, str] = field(default_factory=dict)
    stage_seconds: dict[str, float] = field(default_factory=dict)
    current_stage: str | None = None
    finished: bool = False
    reconciliation_passed: bool | None = None
    error: str | None = None


class RunTracker:
    """Thread-safe registry of runs started by this API process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, TrackedRun] = {}
        self._active: str | None = None

    def get(self, run_id: str) -> TrackedRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return TrackedRun(run.run_id, run.source, dict(run.stage_status),
                              dict(run.stage_seconds), run.current_stage, run.finished,
                              run.reconciliation_passed, run.error)

    def _listener(self, run_id: str):
        def on_stage(event: str, name: str, seconds: float | None) -> None:
            if name not in STAGE_NAMES:
                return
            with self._lock:
                run = self._runs[run_id]
                if event == "start":
                    run.stage_status[name] = "running"
                    run.current_stage = name
                else:
                    run.stage_status[name] = "done" if event == "done" else "failed"
                    if seconds is not None:
                        run.stage_seconds[name] = seconds
        return on_stage

    def start(self, engine: Engine, settings: Settings, input_dir: Path, source: str,
              strict_schema: bool) -> str:
        """Start a pipeline run in a background thread and return its run id."""
        with self._lock:
            if self._active is not None:
                raise PipelineBusyError(f"pipeline run {self._active} is still running")
            run_id = generate_run_id()
            self._runs[run_id] = TrackedRun(run_id=run_id, source=source)
            self._active = run_id

        def work() -> None:
            passed: bool | None = None
            try:
                result = run_pipeline(input_dir, engine=engine, settings=settings, run_id=run_id,
                                      strict_schema=strict_schema,
                                      reports_dir=settings.reports_dir,
                                      stage_listener=self._listener(run_id))
                if result.reconciliation is not None:
                    passed = result.reconciliation.passed
                try:
                    from app.services.reporting import write_quality_report
                    write_quality_report(engine, settings.reports_dir)
                except Exception:
                    log.exception("Could not write the data quality report after run %s", run_id)
            except Exception as exc:
                log.exception("Pipeline run %s crashed before it could record its status", run_id)
                with self._lock:
                    self._runs[run_id].error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    run = self._runs[run_id]
                    run.finished = True
                    run.current_stage = None
                    run.reconciliation_passed = passed
                    self._active = None

        thread = threading.Thread(target=work, name=f"pipeline-{run_id}", daemon=True)
        thread.start()
        log.info("Started pipeline run %s for %s", run_id, input_dir)
        return run_id

    @property
    def active_run(self) -> str | None:
        with self._lock:
            return self._active


tracker = RunTracker()
