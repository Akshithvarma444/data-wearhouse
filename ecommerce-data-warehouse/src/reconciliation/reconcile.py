"""Source-to-target reconciliation and data-quality report.

For one run, compares what arrived (``raw.*`` + ``ops.source_files``) with what
was quarantined (``ops.rejected_records``) and what is published
(``dw.fact_sales`` + dimensions). All money comparisons are exact NUMERIC.

CRITICAL checks must pass; otherwise the run is marked RECONCILIATION_FAILED.
INFO rows explain expected differences (duplicates, previously loaded events).

Outputs
-------
* ``reports/reconciliation_report.csv``                 (latest run)
* ``reports/reconciliation/reconciliation_<run_id>.csv`` (history)
* ``reports/reconciliation_summary.txt``                 (readable summary)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, text

from src.monitoring.logger import get_logger
from src.validation.schemas import ENTITY_LOAD_ORDER, ReasonCode

log = get_logger("reconciliation")

CRITICAL = "CRITICAL"
INFO = "INFO"
_NORMALISE_ID = "regexp_replace(btrim({col}), '\\s+', ' ', 'g')"


@dataclass
class ReconciliationCheck:
    """One reconciliation line item."""

    check_name: str
    severity: str
    source_value: Any
    target_value: Any
    explanation: str
    status: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            if self.severity == INFO:
                self.status = "INFO"
            else:
                self.status = "PASS" if self.source_value == self.target_value else "FAIL"

    @property
    def difference(self) -> Any:
        if isinstance(self.source_value, (int, Decimal)) and isinstance(self.target_value,
                                                                         (int, Decimal)):
            return self.target_value - self.source_value
        return ""


@dataclass
class ReconciliationResult:
    """All checks for one run plus report locations."""

    run_id: str
    checks: list[ReconciliationCheck] = field(default_factory=list)
    report_path: Path | None = None
    summary_path: Path | None = None

    @property
    def passed(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    @property
    def failed_checks(self) -> list[ReconciliationCheck]:
        return [c for c in self.checks if c.status == "FAIL"]


def latest_run_id(engine: Engine) -> str | None:
    """Most recent run that reached the warehouse-load stage or beyond."""
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT run_id FROM ops.pipeline_runs
            WHERE status IN ('SUCCESS', 'RECONCILIATION_FAILED', 'RUNNING')
            ORDER BY start_time DESC LIMIT 1
        """)).scalar()


def _scalar(conn: Connection, sql: str, params: dict[str, Any]) -> Any:
    value = conn.execute(text(sql), params).scalar()
    return value if value is not None else 0


def _entity_checks(conn: Connection, run_id: str) -> list[ReconciliationCheck]:
    checks: list[ReconciliationCheck] = []
    for entity in ENTITY_LOAD_ORDER:
        file_count = int(_scalar(conn, """SELECT record_count FROM ops.source_files
                                          WHERE run_id = :r AND entity_name = :e""",
                                 {"r": run_id, "e": entity}))
        rows = dict(conn.execute(text(f"""
            SELECT validation_status, count(*) FROM raw.{entity}
            WHERE run_id = :r GROUP BY validation_status"""), {"r": run_id}).all())
        valid, rejected, skipped = (int(rows.get(k, 0)) for k in ("VALID", "REJECTED", "SKIPPED"))
        quarantined = int(_scalar(conn, """SELECT count(*) FROM ops.rejected_records
                                           WHERE run_id = :r AND entity_name = :e""",
                                  {"r": run_id, "e": entity}))
        checks.append(ReconciliationCheck(
            f"{entity}: input = valid + rejected + skipped", CRITICAL, file_count,
            valid + rejected + skipped,
            f"file records={file_count}; valid={valid}, rejected={rejected}, skipped={skipped}"))
        checks.append(ReconciliationCheck(
            f"{entity}: rejected rows = quarantine rows", CRITICAL, rejected, quarantined,
            "every rejected raw record has exactly one ops.rejected_records row"))
    return checks


def reconcile_run(engine: Engine, run_id: str, reports_dir: Path | None = None,
                  source_system: str = "ecommerce_oltp") -> ReconciliationResult:
    """Run every reconciliation check for ``run_id`` and (optionally) write the reports."""
    result = ReconciliationResult(run_id=run_id)
    p = {"r": run_id, "s": source_system}
    item_id = _NORMALISE_ID.format(col="ri.order_item_id")
    valid_items = f"""
        SELECT {item_id} AS order_item_id,
               btrim(ri.quantity)::integer AS quantity,
               btrim(ri.unit_price)::numeric(12,2) AS unit_price,
               COALESCE(NULLIF(btrim(ri.discount_amount), '')::numeric(12,2), 0) AS discount
        FROM raw.order_items ri
        WHERE ri.run_id = :r AND ri.validation_status = 'VALID'"""

    with engine.connect() as conn:
        exists = conn.execute(text("SELECT status FROM ops.pipeline_runs WHERE run_id = :r"),
                              {"r": run_id}).scalar()
        if exists is None:
            raise ValueError(f"run '{run_id}' not found in ops.pipeline_runs")
        checks = result.checks
        checks.extend(_entity_checks(conn, run_id))

        source_items = int(_scalar(conn, "SELECT count(*) FROM raw.order_items WHERE run_id = :r", p))
        valid_count = int(_scalar(conn, f"SELECT count(*) FROM ({valid_items}) v", p))
        rejected_items = int(_scalar(conn, """SELECT count(*) FROM ops.rejected_records
                                              WHERE run_id = :r AND entity_name = 'order_items'""", p))
        checks.append(ReconciliationCheck("order_items: source record count", INFO, source_items,
                                          source_items, "records read from order_items.csv"))
        checks.append(ReconciliationCheck("order_items: rejected record count", INFO,
                                          rejected_items, rejected_items,
                                          "quarantined in ops.rejected_records"))

        totals = conn.execute(text(f"""
            WITH v AS ({valid_items})
            SELECT count(f.sales_key),
                   COALESCE(sum(v.quantity * v.unit_price), 0),
                   COALESCE(sum(f.gross_sales), 0),
                   COALESCE(sum(v.discount), 0),
                   COALESCE(sum(f.discount_amount), 0),
                   COALESCE(sum(v.quantity * v.unit_price - v.discount), 0),
                   COALESCE(sum(f.net_revenue), 0)
            FROM v LEFT JOIN dw.fact_sales f
              ON f.source_system = :s AND f.order_item_id = v.order_item_id
        """), p).one()
        loaded, src_gross, tgt_gross, src_disc, tgt_disc, src_net, tgt_net = totals
        checks.append(ReconciliationCheck(
            "valid source order items = fact rows present", CRITICAL, valid_count, int(loaded),
            "each valid order line of this run exists exactly once in dw.fact_sales"))
        checks.append(ReconciliationCheck("total gross sales (valid source vs fact)", CRITICAL,
                                          Decimal(src_gross), Decimal(tgt_gross),
                                          "sum(quantity * unit_price), exact NUMERIC"))
        checks.append(ReconciliationCheck("total discounts (valid source vs fact)", CRITICAL,
                                          Decimal(src_disc), Decimal(tgt_disc),
                                          "sum(discount_amount)"))
        checks.append(ReconciliationCheck("total net revenue (valid source vs fact)", CRITICAL,
                                          Decimal(src_net), Decimal(tgt_net),
                                          "sum(gross - discount)"))

        duplicates = int(_scalar(conn, """
            SELECT count(*) FROM ops.rejected_records WHERE run_id = :r
              AND reason_codes && ARRAY[:exact, :conflict]::text[]""",
            {**p, "exact": ReasonCode.EXACT_DUPLICATE, "conflict": ReasonCode.CONFLICTING_DUPLICATE}))
        checks.append(ReconciliationCheck(
            "duplicate records quarantined (all entities)", INFO, duplicates, duplicates,
            "exact duplicates: first copy kept, later copies rejected; conflicting duplicates: "
            "all copies rejected"))

        metrics = dict(conn.execute(text("""
            SELECT metric_name, metric_value FROM ops.pipeline_metrics
            WHERE run_id = :r AND metric_name IN ('facts_inserted', 'facts_updated',
                                                   'facts_unchanged', 'facts_rekeyed')"""),
                                    p).all())
        inserted = int(metrics.get("facts_inserted", 0))
        updated = int(metrics.get("facts_updated", 0))
        unchanged = int(metrics.get("facts_unchanged", 0))
        checks.append(ReconciliationCheck(
            "fact rows inserted / updated / unchanged", INFO,
            f"{inserted} / {updated} / {unchanged}", f"{inserted} / {updated} / {unchanged}",
            "unchanged = events already loaded by an earlier run with identical content "
            "(idempotent re-run, not re-inserted); updated = corrected events or rebuilt lines "
            "of changed orders"))

        missing_refs = int(_scalar(conn, f"""
            WITH v AS ({valid_items})
            SELECT count(*) FROM v
            JOIN dw.fact_sales f ON f.source_system = :s AND f.order_item_id = v.order_item_id
            LEFT JOIN dw.dim_customer c ON c.customer_key = f.customer_key
            LEFT JOIN dw.dim_product p ON p.product_key = f.product_key
            LEFT JOIN dw.dim_date d ON d.date_key = f.date_key
            WHERE c.customer_key IS NULL OR p.product_key IS NULL OR d.date_key IS NULL""", p))
        checks.append(ReconciliationCheck("missing dimension references", CRITICAL, 0, missing_refs,
                                          "facts whose customer/product/date key has no dimension row"))

        wrong_version = int(_scalar(conn, """
            SELECT count(*) FROM dw.fact_sales f
            JOIN dw.dim_customer c ON c.customer_key = f.customer_key
            JOIN dw.dim_product p ON p.product_key = f.product_key
            JOIN source.orders o ON o.order_id = f.order_id
            JOIN source.order_items oi ON oi.order_item_id = f.order_item_id
            WHERE f.source_system = :s AND (
                  NOT (f.order_timestamp >= c.effective_start_date
                       AND f.order_timestamp < c.effective_end_date)
               OR NOT (f.order_timestamp >= p.effective_start_date
                       AND f.order_timestamp < p.effective_end_date)
               OR c.customer_id <> o.customer_id
               OR p.product_id <> oi.product_id
               OR f.date_key <> to_char(f.order_timestamp, 'YYYYMMDD')::integer)""", p))
        checks.append(ReconciliationCheck(
            "facts linked to wrong dimension version / date", CRITICAL, 0, wrong_version,
            "point-in-time check over the whole fact table: order timestamp inside the "
            "version's effective range, business keys match the source"))

        ods_count, ods_net = conn.execute(text("""
            SELECT count(*), COALESCE(sum(quantity * unit_price - discount_amount), 0)
            FROM source.order_items""")).one()
        fact_count, fact_net = conn.execute(text("""
            SELECT count(*), COALESCE(sum(net_revenue), 0) FROM dw.fact_sales
            WHERE source_system = :s"""), p).one()
        checks.append(ReconciliationCheck("warehouse total: source order items = fact rows",
                                          CRITICAL, int(ods_count), int(fact_count),
                                          "all-time: source.order_items vs dw.fact_sales"))
        checks.append(ReconciliationCheck("warehouse total: source net revenue = fact net revenue",
                                          CRITICAL, Decimal(ods_net), Decimal(fact_net),
                                          "all-time, exact NUMERIC"))

        multi_current = int(_scalar(conn, """
            SELECT count(*) FROM (
                SELECT customer_id FROM dw.dim_customer WHERE is_current
                GROUP BY customer_id HAVING count(*) > 1
                UNION ALL
                SELECT product_id FROM dw.dim_product WHERE is_current
                GROUP BY product_id HAVING count(*) > 1) x""", p))
        checks.append(ReconciliationCheck("business keys with more than one current version",
                                          CRITICAL, 0, multi_current, "SCD2 integrity"))

    if reports_dir is not None:
        write_reports(result, reports_dir)
    level = log.info if result.passed else log.error
    level("Reconciliation for %s: %s (%d checks, %d failed)", run_id,
          "PASSED" if result.passed else "FAILED", len(result.checks), len(result.failed_checks))
    return result


def format_reconciliation_summary(result: ReconciliationResult) -> str:
    """Readable multi-line summary."""
    lines = ["", "=" * 96, f"Reconciliation Report - run {result.run_id}", "=" * 96,
             f"{'Status':<6} {'Sev':<8} {'Check':<55} {'Source':>11} {'Target':>11}", "-" * 96]
    for c in result.checks:
        lines.append(f"{c.status:<6} {c.severity:<8} {c.check_name[:55]:<55} "
                     f"{str(c.source_value)[:11]:>11} {str(c.target_value)[:11]:>11}")
    lines.append("-" * 96)
    lines.append("OVERALL: " + ("PASSED - source and target reconcile" if result.passed
                                else f"FAILED - {len(result.failed_checks)} critical check(s) failed"))
    lines.append("=" * 96)
    return "\n".join(lines)


def write_reports(result: ReconciliationResult, reports_dir: Path) -> None:
    """Write CSV (latest + per-run history) and the readable summary."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    history = reports_dir / "reconciliation"
    history.mkdir(exist_ok=True)
    header = ["run_id", "check_name", "severity", "status", "source_value", "target_value",
              "difference", "explanation"]
    rows = [[result.run_id, c.check_name, c.severity, c.status, c.source_value, c.target_value,
             c.difference, c.explanation] for c in result.checks]
    latest = reports_dir / "reconciliation_report.csv"
    for path in (latest, history / f"reconciliation_{result.run_id}.csv"):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
    summary = reports_dir / "reconciliation_summary.txt"
    summary.write_text(format_reconciliation_summary(result) + "\n", encoding="utf-8")
    result.report_path, result.summary_path = latest, summary
