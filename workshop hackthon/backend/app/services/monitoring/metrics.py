"""Pipeline run bookkeeping (ops.pipeline_runs / ops.pipeline_metrics) and stage timing.

Each function here uses its *own* short transaction, so run metadata is written
even when the warehouse transaction fails and is rolled back.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.services.monitoring.logger import get_logger

log = get_logger("metrics")


class DuplicateRunIdError(ValueError):
    """The supplied run identifier was already used."""


StageListener = Callable[[str, str, float | None], None]
"""listener(event, stage_name, seconds): event is 'start', 'done' or 'failed'."""


class StageTimer:
    """Collects wall-clock durations of named pipeline stages.

    An optional ``listener`` is notified when each stage starts and ends; the API
    uses it to report the real current stage of a running pipeline.
    """

    def __init__(self, listener: StageListener | None = None) -> None:
        self.timings: dict[str, float] = {}
        self._listener = listener

    def _notify(self, event: str, name: str, seconds: float | None) -> None:
        if self._listener is None:
            return
        try:
            self._listener(event, name, seconds)
        except Exception:  # a broken listener must never break the pipeline
            log.exception("Stage listener failed for %s/%s", event, name)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        log.debug("Stage '%s' started", name)
        self._notify("start", name, None)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = time.perf_counter() - started
            self.timings[name] = round(self.timings.get(name, 0.0) + elapsed, 4)
            self._notify("failed" if failed else "done", name, round(elapsed, 4))
            log.info("Stage '%s' %s in %.3fs", name, "failed" if failed else "finished", elapsed)


@dataclass
class RunCounts:
    """Counters stored on ops.pipeline_runs."""

    input_count: int = 0
    valid_count: int = 0
    rejected_count: int = 0
    skipped_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    failed_count: int = 0


@dataclass
class RunSummary:
    """Everything needed to print the end-of-run summary."""

    run_id: str
    status: str
    source: str
    counts: RunCounts
    duration_seconds: float
    entity_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    stage_timings: dict[str, float] = field(default_factory=dict)
    reconciliation_passed: bool | None = None
    error_message: str | None = None


def start_run(engine: Engine, run_id: str, pipeline_name: str, source: str) -> datetime:
    """Insert the RUNNING row. Raises DuplicateRunIdError if the id was used before."""
    started = datetime.now(timezone.utc)
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO ops.pipeline_runs (run_id, pipeline_name, start_time, status, source_file)
                VALUES (:run_id, :name, :start, 'RUNNING', :source)
            """), {"run_id": run_id, "name": pipeline_name, "start": started, "source": source})
    except IntegrityError as exc:
        raise DuplicateRunIdError(f"run_id '{run_id}' already exists; use a new run id") from exc
    return started


def finish_run(engine: Engine, run_id: str, status: str, counts: RunCounts,
               duration_seconds: float, error_message: str | None = None) -> None:
    """Close the run row with final status, counters and duration."""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE ops.pipeline_runs SET
                end_time = now(), status = :status, input_count = :input_count,
                valid_count = :valid_count, rejected_count = :rejected_count,
                inserted_count = :inserted_count, updated_count = :updated_count,
                failed_count = :failed_count, execution_duration_seconds = :duration,
                error_message = :error
            WHERE run_id = :run_id
        """), {"status": status, "input_count": counts.input_count,
               "valid_count": counts.valid_count, "rejected_count": counts.rejected_count,
               "inserted_count": counts.inserted_count, "updated_count": counts.updated_count,
               "failed_count": counts.failed_count, "duration": round(duration_seconds, 3),
               "error": (error_message or None) and error_message[:4000], "run_id": run_id})


def record_metrics(engine: Engine, run_id: str, metrics: dict[str, float | int]) -> int:
    """Upsert name/value metrics for a run. Returns the number written."""
    rows = [{"run_id": run_id, "name": name, "value": float(value)}
            for name, value in sorted(metrics.items()) if value is not None]
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ops.pipeline_metrics (run_id, metric_name, metric_value)
            VALUES (:run_id, :name, :value)
            ON CONFLICT (run_id, metric_name)
            DO UPDATE SET metric_value = EXCLUDED.metric_value, recorded_at = now()
        """), rows)
    return len(rows)


def format_run_summary(summary: RunSummary) -> str:
    """Human-readable end-of-run report (values come from the actual execution)."""
    c = summary.counts
    lines = [
        "",
        "=" * 64,
        "Pipeline Execution Summary",
        "=" * 64,
        f"Run ID              : {summary.run_id}",
        f"Status              : {summary.status}",
        f"Source              : {summary.source}",
        f"Input Records       : {c.input_count:,}",
        f"Valid Records       : {c.valid_count:,}",
        f"Rejected Records    : {c.rejected_count:,}",
    ]
    if c.skipped_count:
        lines.append(f"Skipped (backfill)  : {c.skipped_count:,}")
    lines += [
        f"Inserted Fact Rows  : {c.inserted_count:,}",
        f"Updated Fact Rows   : {c.updated_count:,}",
        f"Unchanged Fact Rows : {c.unchanged_count:,}",
        f"Execution Time      : {summary.duration_seconds:.3f}s",
    ]
    if summary.reconciliation_passed is not None:
        lines.append("Reconciliation      : "
                     + ("PASSED" if summary.reconciliation_passed else "FAILED"))
    if summary.entity_counts:
        lines += ["", f"{'Entity':<12}{'Input':>10}{'Valid':>10}{'Rejected':>10}{'Skipped':>10}"]
        for entity, ec in summary.entity_counts.items():
            lines.append(f"{entity:<12}{ec.get('input', 0):>10,}{ec.get('valid', 0):>10,}"
                         f"{ec.get('rejected', 0):>10,}{ec.get('skipped', 0):>10,}")
    if summary.stage_timings:
        lines += ["", "Stage timings (s): " + ", ".join(
            f"{k}={v:.3f}" for k, v in summary.stage_timings.items())]
    if summary.error_message:
        lines += ["", f"Error: {summary.error_message}"]
    lines.append("=" * 64)
    return "\n".join(lines)
