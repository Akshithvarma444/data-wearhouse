"""End-to-end pipeline orchestration.

    extract -> validate -> land raw + quarantine (commit) -> transform
            -> [ ONE transaction: source upserts -> SCD2 dims -> date dim
                 -> fact build + point-in-time key resolution -> fact upsert -> re-key ]
            -> reconcile -> run status + metrics

Failure handling
----------------
* The run row is created first (status RUNNING) in its own transaction.
* Raw records and quarantined rows are committed before the warehouse load, so
  they remain available for investigation if the load fails.
* The warehouse transaction is all-or-nothing; on any exception PostgreSQL
  rolls it back and the run is closed as FAILED with the error message.
* Retrying is simply running the pipeline again with a new run id: every write
  is an idempotent upsert, so a retry converges to the same final state.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from app.config.settings import Settings, get_settings
from app.services.ingestion.extract import extract_all
from app.database.connection import get_engine
from app.services.loading.load import (DataIntegrityError, DbReferenceLookup, acquire_load_lock,
                              fetch_dimension_versions, fetch_lines_of_changed_orders,
                              fetch_order_headers, land_raw_and_rejects, load_dim_customer,
                              load_dim_date, load_dim_product, realign_fact_keys,
                              upsert_fact_sales, upsert_source_customers,
                              upsert_source_order_items, upsert_source_orders,
                              upsert_source_products)
from app.services.monitoring.logger import get_logger
from app.services.monitoring.metrics import (RunCounts, RunSummary, StageListener, StageTimer,
                                             finish_run, format_run_summary, record_metrics,
                                             start_run)
from app.services.reconciliation.reconcile import ReconciliationResult, reconcile_run
from app.services.transformation.dimensions import (date_range_for, generate_date_dimension,
                                           transform_customers, transform_products)
from app.services.transformation.facts import (resolve_surrogate_keys, transform_order_items,
                                      transform_orders, transform_sales)
from app.services.validation.schemas import ENTITY_LOAD_ORDER
from app.services.validation.validate import EntityValidationResult, parse_timestamps, validate_all

log = get_logger("pipeline")


@dataclass
class TransformedBatch:
    """Typed, cleaned frames ready for loading."""

    customers: pd.DataFrame
    products: pd.DataFrame
    orders: pd.DataFrame
    order_items: pd.DataFrame
    files: dict[str, str]


@dataclass
class WarehouseLoadStats:
    """Counters produced by the warehouse transaction."""

    metrics: dict[str, int] = field(default_factory=dict)
    facts_inserted: int = 0
    facts_updated: int = 0
    facts_unchanged: int = 0


@dataclass
class PipelineResult:
    """What a pipeline execution returns to its caller."""

    run_id: str
    status: str
    summary: RunSummary
    reconciliation: ReconciliationResult | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    stage_timings: dict[str, float] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCESS"


def generate_run_id() -> str:
    """Sortable, unique run identifier, e.g. run_20251111T101500Z_1a2b3c4d."""
    return f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


# ----------------------------------------------------------------------------- backfill window
def apply_backfill_window(results: dict[str, EntityValidationResult], lookup: DbReferenceLookup,
                          from_date: date | None, to_date: date | None) -> None:
    """Restrict orders/order lines to order dates in [from_date, to_date] (inclusive).

    Out-of-window records are valid but *skipped* (landed with status SKIPPED and
    explained by reconciliation). Customers and products are always loaded.
    """
    if from_date is None and to_date is None:
        return
    lower = pd.Timestamp(from_date) if from_date else pd.Timestamp.min
    upper = (pd.Timestamp(to_date) + pd.Timedelta(days=1)) if to_date else pd.Timestamp.max

    orders = results["orders"]
    order_ts = parse_timestamps(orders.valid["order_date"])
    keep = ((order_ts >= lower) & (order_ts < upper)).to_numpy()
    batch_dates = dict(zip(orders.valid["order_id"], order_ts))
    orders.skipped = orders.valid.loc[~keep].reset_index(drop=True)
    orders.valid = orders.valid.loc[keep].reset_index(drop=True)

    items = results["order_items"]
    unknown = set(items.valid["order_id"]) - set(batch_dates)
    db_dates = lookup.order_dates(unknown)
    item_dates = items.valid["order_id"].map(lambda oid: batch_dates.get(oid, db_dates.get(oid)))
    item_ts = pd.to_datetime(item_dates)
    keep_items = ((item_ts >= lower) & (item_ts < upper)).fillna(False).to_numpy(dtype=bool)
    items.skipped = items.valid.loc[~keep_items].reset_index(drop=True)
    items.valid = items.valid.loc[keep_items].reset_index(drop=True)
    log.info("Backfill window %s..%s: skipped %d order(s) and %d order line(s)",
             from_date, to_date, len(orders.skipped), len(items.skipped))


# ----------------------------------------------------------------------------- stages
def transform_batch(results: dict[str, EntityValidationResult]) -> TransformedBatch:
    """Dimension inputs + typed source frames (pure, no database access)."""
    files = {name: res.source_file for name, res in results.items()}
    return TransformedBatch(
        customers=transform_customers(results["customers"].valid),
        products=transform_products(results["products"].valid),
        orders=transform_orders(results["orders"].valid),
        order_items=transform_order_items(results["order_items"].valid, files["order_items"]),
        files=files,
    )


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return frames[0]
    return non_empty[0] if len(non_empty) == 1 else pd.concat(non_empty, ignore_index=True)


def load_warehouse(engine: Engine, run_id: str, batch: TransformedBatch, source_system: str,
                   timer: StageTimer) -> WarehouseLoadStats:
    """Publish the batch atomically: source, dimensions, date dimension and facts."""
    stats = WarehouseLoadStats()
    m = stats.metrics
    with engine.begin() as conn:  # single transaction - commit on success, rollback on error
        acquire_load_lock(conn)
        with timer.stage("load_source"):
            m["source_customers_inserted"], m["source_customers_updated"] = upsert_source_customers(
                conn, batch.customers, run_id, batch.files["customers"])
            m["source_products_inserted"], m["source_products_updated"] = upsert_source_products(
                conn, batch.products, run_id, batch.files["products"])
            m["source_orders_inserted"], m["source_orders_updated"], changed_orders = \
                upsert_source_orders(conn, batch.orders, run_id, batch.files["orders"])
            m["source_order_items_inserted"], m["source_order_items_updated"] = \
                upsert_source_order_items(conn, batch.order_items, run_id)

        with timer.stage("load_dimensions"):
            customer_stats = load_dim_customer(conn, batch.customers, run_id)
            product_stats = load_dim_product(conn, batch.products, run_id)
            for prefix, s in (("dim_customer", customer_stats), ("dim_product", product_stats)):
                m[f"{prefix}_new_members"] = s.new_members
                m[f"{prefix}_new_versions"] = s.new_versions
                m[f"{prefix}_type1_updates"] = s.type1_updates
                m[f"{prefix}_stale_ignored"] = s.stale_ignored

        with timer.stage("transform_facts"):
            extra_lines = fetch_lines_of_changed_orders(conn, changed_orders)
            lines = _concat([batch.order_items, extra_lines])
            # Headers come from source.orders AFTER the upsert, i.e. the version that won.
            # A batch header older than the stored one was ignored by the upsert, so
            # building facts from the batch copy would link lines to a stale customer.
            headers = fetch_order_headers(conn, set(lines["order_id"]))
            sales = transform_sales(lines, headers)
            m["fact_lines_rebuilt_from_changed_orders"] = len(extra_lines)

        with timer.stage("load_dim_date"):
            span = date_range_for(sales["order_timestamp"])
            dates = generate_date_dimension(*span) if span else pd.DataFrame()
            m["dim_date_inserted"] = load_dim_date(conn, dates)

        with timer.stage("resolve_keys"):
            customer_versions = fetch_dimension_versions(conn, "customer",
                                                         set(sales["customer_id"]))
            product_versions = fetch_dimension_versions(conn, "product", set(sales["product_id"]))
            resolved, unresolved = resolve_surrogate_keys(sales, customer_versions,
                                                          product_versions)
            if not unresolved.empty:
                sample = unresolved["order_item_id"].head(5).tolist()
                raise DataIntegrityError(
                    f"{len(unresolved)} fact row(s) could not be linked to a dimension version "
                    f"(e.g. {sample}); the batch is rolled back")

        with timer.stage("load_facts"):
            ins, upd, unch = upsert_fact_sales(conn, resolved, run_id, source_system)
            rekeyed = (realign_fact_keys(conn, "customer", customer_stats.changed_business_keys,
                                         run_id)
                       + realign_fact_keys(conn, "product", product_stats.changed_business_keys,
                                           run_id))
            stats.facts_inserted, stats.facts_updated, stats.facts_unchanged = ins, upd, unch
            m.update({"facts_inserted": ins, "facts_updated": upd, "facts_unchanged": unch,
                      "facts_rekeyed": rekeyed})
    return stats


# ----------------------------------------------------------------------------- entry point
def run_pipeline(input_dir: Path | str, *, engine: Engine | None = None,
                 settings: Settings | None = None, run_id: str | None = None,
                 batch_size: int | None = None, strict_schema: bool = False,
                 from_date: date | None = None, to_date: date | None = None,
                 reports_dir: Path | None = None,
                 stage_listener: StageListener | None = None) -> PipelineResult:
    """Run the complete pipeline once. Never raises for data/load errors - the
    returned ``PipelineResult.status`` is SUCCESS, FAILED or RECONCILIATION_FAILED.

    ``stage_listener`` (optional) is called as each stage starts and finishes."""
    settings = settings or get_settings()
    own_engine = engine is None
    engine = engine or get_engine(settings.database_url)
    run_id = run_id or generate_run_id()
    batch_size = batch_size or settings.batch_size
    reports_dir = reports_dir or settings.reports_dir
    source = str(Path(input_dir).resolve())
    timer = StageTimer(stage_listener)
    counts = RunCounts()
    entity_counts: dict[str, dict[str, int]] = {}
    metrics: dict[str, float] = {}
    reconciliation: ReconciliationResult | None = None
    status, error = "FAILED", None
    started = time.perf_counter()

    log.info("Pipeline %s started for %s (batch_size=%d)", run_id, source, batch_size)
    start_run(engine, run_id, settings.pipeline_name, source)
    try:
        with timer.stage("extract"):
            extracted = extract_all(input_dir, strict_schema=strict_schema)
        counts.input_count = sum(e.record_count for e in extracted.values())
        for entity in extracted.values():
            if entity.unexpected_columns:
                metrics[f"schema_drift_columns_{entity.entity_name}"] = len(entity.unexpected_columns)

        lookup = DbReferenceLookup(engine)
        with timer.stage("validate"):
            results = validate_all(extracted, lookup, batch_size=batch_size)
            apply_backfill_window(results, lookup, from_date, to_date)
        for name in ENTITY_LOAD_ORDER:
            res = results[name]
            entity_counts[name] = {"input": res.input_count, "valid": res.valid_count,
                                   "rejected": res.rejected_count, "skipped": res.skipped_count}
            metrics[f"{name}_input"] = res.input_count
            metrics[f"{name}_valid"] = res.valid_count
            metrics[f"{name}_rejected"] = res.rejected_count
            for code, n in res.reason_counts().items():
                metrics[f"rejected_{name}_{code.lower()}"] = n
        counts.valid_count = sum(r.valid_count for r in results.values())
        counts.rejected_count = sum(r.rejected_count for r in results.values())
        counts.skipped_count = sum(r.skipped_count for r in results.values())

        with timer.stage("land_raw_and_quarantine"):
            land_raw_and_rejects(engine, run_id, extracted, results)

        with timer.stage("transform"):
            batch = transform_batch(results)

        with timer.stage("load_warehouse"):
            wh = load_warehouse(engine, run_id, batch, settings.source_system, timer)
        counts.inserted_count, counts.updated_count = wh.facts_inserted, wh.facts_updated
        counts.unchanged_count = wh.facts_unchanged
        metrics.update(wh.metrics)
        record_metrics(engine, run_id, metrics)  # reconciliation reads the fact counters

        with timer.stage("reconcile"):
            reconciliation = reconcile_run(engine, run_id, reports_dir, settings.source_system)
        if reconciliation.passed:
            status = "SUCCESS"
        else:
            status = "RECONCILIATION_FAILED"
            error = "critical reconciliation check(s) failed: " + "; ".join(
                c.check_name for c in reconciliation.failed_checks)
    except Exception as exc:  # recorded on the run row and re-surfaced via the result
        status, error = "FAILED", f"{type(exc).__name__}: {exc}"
        counts.failed_count = counts.valid_count
        log.exception("Pipeline %s failed: %s", run_id, error)
    finally:
        duration = time.perf_counter() - started
        for stage_name, seconds in timer.timings.items():
            metrics[f"{stage_name}_seconds"] = seconds
        metrics["total_seconds"] = round(duration, 4)
        fact_input = metrics.get("order_items_input", 0)
        if duration > 0 and fact_input:
            metrics["order_items_per_second"] = round(fact_input / duration, 2)
        try:
            record_metrics(engine, run_id, metrics)
            finish_run(engine, run_id, status, counts, duration, error)
        except Exception:
            log.exception("Could not record final status of run %s", run_id)
            raise
        finally:
            if own_engine:
                engine.dispose()

    summary = RunSummary(run_id=run_id, status=status, source=source, counts=counts,
                         duration_seconds=duration, entity_counts=entity_counts,
                         stage_timings=dict(timer.timings),
                         reconciliation_passed=None if reconciliation is None
                         else reconciliation.passed, error_message=error)
    log_fn = log.info if status == "SUCCESS" else log.error
    log_fn("Pipeline %s finished with status %s in %.3fs", run_id, status, duration)
    return PipelineResult(run_id=run_id, status=status, summary=summary,
                          reconciliation=reconciliation, metrics=metrics,
                          stage_timings=dict(timer.timings))


def print_summary(result: PipelineResult) -> str:
    """Return (and log) the formatted run summary."""
    text_summary = format_run_summary(result.summary)
    log.info(text_summary)
    return text_summary


def latest_runs(engine: Engine, limit: int = 10) -> list[dict[str, object]]:
    """Most recent runs from ops.pipeline_runs (for the CLI ``runs`` command)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT run_id, status, start_time, execution_duration_seconds, input_count,
                   valid_count, rejected_count, inserted_count, updated_count, error_message
            FROM ops.pipeline_runs ORDER BY start_time DESC LIMIT :n"""), {"n": limit})
        return [dict(r._mapping) for r in rows]


__all__ = ["PipelineResult", "TransformedBatch", "run_pipeline", "print_summary",
           "generate_run_id", "apply_backfill_window", "transform_batch", "load_warehouse",
           "latest_runs"]
