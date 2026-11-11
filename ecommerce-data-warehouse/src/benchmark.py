"""Performance benchmark: generate -> run pipeline -> idempotent re-run, per data size.

Runs against an isolated ``<DB_NAME>_bench`` database that is reset for every
size, so the developer's warehouse is never touched. All numbers in the report
are measured during the run - nothing is estimated.

Also runs one optimisation experiment: loading the same fact rows with
row-by-row ``INSERT`` (executemany) versus PostgreSQL ``COPY``.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy import Engine, text

from src.config.settings import PROJECT_ROOT, get_settings
from src.generators.generate_data import GeneratorConfig, generate_dataset, write_dataset
from src.loading.database import (copy_rows, derive_database_url, ensure_database_exists,
                                  get_engine, init_db)
from src.loading.load import FACT_STAGE_COLUMNS, _create_temp, frame_rows
from src.monitoring.logger import LOGGER_NAME, get_logger

log = get_logger("benchmark")

STAGE_GROUPS: dict[str, tuple[str, ...]] = {
    "extract": ("extract",),
    "validate": ("validate",),
    "transform": ("transform", "transform_facts", "resolve_keys"),
    "load": ("land_raw_and_quarantine", "load_source", "load_dimensions", "load_dim_date",
             "load_facts"),
    "reconcile": ("reconcile",),
}


@dataclass
class BenchmarkRow:
    order_items: int
    customers: int
    products: int
    orders: int
    generate_s: float
    stage_s: dict[str, float]
    total_s: float
    rerun_s: float
    rerun_inserted: int
    rerun_unchanged: int
    status: str

    @property
    def rows_per_second(self) -> float:
        return self.order_items / self.total_s if self.total_s else 0.0


def _sizes_for(order_items: int) -> tuple[int, int]:
    """Customers and products scaled with the fact volume."""
    return max(100, order_items // 10), max(50, order_items // 100)


def _group_timings(timings: dict[str, float]) -> dict[str, float]:
    return {group: round(sum(timings.get(s, 0.0) for s in stages), 3)
            for group, stages in STAGE_GROUPS.items()}


def insert_vs_copy(engine: Engine, n_rows: int) -> tuple[int, float, float]:
    """Load the same fact rows into a temp table via executemany INSERT and via COPY.

    Both runs happen inside transactions that are rolled back. Returns
    ``(rows, insert_seconds, copy_seconds)``.
    """
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT {", ".join(FACT_STAGE_COLUMNS)} FROM dw.fact_sales
            ORDER BY sales_key LIMIT :n"""), {"n": n_rows})
        frame = pd.DataFrame(result.all(), columns=list(result.keys()))
    rows = list(frame_rows(frame, FACT_STAGE_COLUMNS))
    ddl = ("order_item_id varchar(30), order_id varchar(30), customer_key bigint, "
           "product_key bigint, date_key integer, order_timestamp timestamp, "
           "order_status varchar(20), payment_method varchar(30), quantity integer, "
           "unit_price numeric(12,2), gross_sales numeric(14,2), discount_amount numeric(12,2), "
           "net_revenue numeric(14,2), source_file text")
    insert_sql = text(f"INSERT INTO tmp_bench ({', '.join(FACT_STAGE_COLUMNS)}) VALUES ("
                      + ", ".join(f":{c}" for c in FACT_STAGE_COLUMNS) + ")")
    params = [dict(zip(FACT_STAGE_COLUMNS, r)) for r in rows]

    with engine.connect() as conn:
        trans = conn.begin()
        _create_temp(conn, "tmp_bench", ddl)
        started = time.perf_counter()
        for p in params:  # row-by-row: one statement per row (naive ETL pattern)
            conn.execute(insert_sql, p)
        insert_s = time.perf_counter() - started
        trans.rollback()
    with engine.connect() as conn:
        trans = conn.begin()
        _create_temp(conn, "tmp_bench", ddl)
        started = time.perf_counter()
        copy_rows(conn, "tmp_bench", FACT_STAGE_COLUMNS, rows)
        copy_s = time.perf_counter() - started
        trans.rollback()
    return len(rows), insert_s, copy_s


def run_benchmark(sizes: list[int], seed: int = 42, reuse_data: bool = False,
                  report_path: Path | None = None) -> Path:
    """Execute the benchmark and write reports/performance_report.txt."""
    from src.pipeline import run_pipeline

    settings = get_settings()
    report_path = report_path or settings.reports_dir / "performance_report.txt"
    bench_url = derive_database_url(settings.database_url, "_bench")
    ensure_database_exists(bench_url)
    engine = get_engine(bench_url)
    console = [h for h in logging.getLogger(LOGGER_NAME).handlers
               if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
    previous = [h.level for h in console]
    for handler in console:
        handler.setLevel(logging.WARNING)  # keep the console readable; file log stays INFO

    rows: list[BenchmarkRow] = []
    experiment: tuple[int, float, float] | None = None
    try:
        for n in sorted(sizes):
            customers, products = _sizes_for(n)
            out_dir = PROJECT_ROOT / "data" / "generated" / f"bench_{n}"
            started = time.perf_counter()
            if not (reuse_data and (out_dir / "order_items.csv").exists()):
                frames = generate_dataset(GeneratorConfig(customers=customers, products=products,
                                                          orders=None, order_items=n, seed=seed))
                write_dataset(frames, out_dir)
            generate_s = time.perf_counter() - started
            n_orders = sum(1 for _ in (out_dir / "orders.csv").open(encoding="utf-8")) - 1

            init_db(engine, reset=True)
            print(f"[benchmark] {n:,} order items: running pipeline ...", flush=True)
            first = run_pipeline(out_dir, engine=engine, settings=settings,
                                 reports_dir=settings.reports_dir / "benchmark")
            second = run_pipeline(out_dir, engine=engine, settings=settings,
                                  reports_dir=settings.reports_dir / "benchmark")
            rows.append(BenchmarkRow(
                order_items=n, customers=customers, products=products, orders=n_orders,
                generate_s=round(generate_s, 3), stage_s=_group_timings(first.stage_timings),
                total_s=round(first.summary.duration_seconds, 3),
                rerun_s=round(second.summary.duration_seconds, 3),
                rerun_inserted=second.summary.counts.inserted_count,
                rerun_unchanged=second.summary.counts.unchanged_count,
                status=f"{first.status}/{second.status}"))
            print(f"[benchmark] {n:,}: total {first.summary.duration_seconds:.2f}s, "
                  f"re-run {second.summary.duration_seconds:.2f}s ({first.status}/{second.status})",
                  flush=True)
        experiment_rows = min(10_000, max(sizes))
        experiment = insert_vs_copy(engine, experiment_rows)
        with engine.connect() as conn:
            pg_version = conn.execute(text("SHOW server_version")).scalar()
    finally:
        for handler, level in zip(console, previous):
            handler.setLevel(level)
        engine.dispose()

    report = _format_report(rows, experiment, str(pg_version), seed)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    results = {  # machine-readable copy for the dashboard
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "postgres": str(pg_version),
        "rows": [{"order_items": r.order_items, "orders": r.orders,
                  "generate_s": r.generate_s, **{f"{k}_s": v for k, v in r.stage_s.items()},
                  "total_s": r.total_s, "rows_per_second": round(r.rows_per_second, 1),
                  "rerun_s": r.rerun_s, "rerun_inserted": r.rerun_inserted,
                  "status": r.status} for r in rows],
        "experiment": None if experiment is None else {
            "rows": experiment[0], "insert_s": round(experiment[1], 3),
            "copy_s": round(experiment[2], 3)},
    }
    (report_path.parent / "performance_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    return report_path


def _format_report(rows: list[BenchmarkRow], experiment: tuple[int, float, float] | None,
                   pg_version: str, seed: int) -> str:
    lines = [
        "E-Commerce Sales Data Warehouse - Performance Report",
        "=" * 100,
        f"Generated at      : {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC",
        f"Platform          : {platform.platform()} | CPUs: {os.cpu_count()}",
        f"Python            : {platform.python_version()} | pandas {pd.__version__} | "
        f"numpy {np.__version__} | SQLAlchemy {sqlalchemy.__version__}",
        f"PostgreSQL        : {pg_version} (Docker container, same machine)",
        f"Data              : synthetic, seed={seed}; customers = n/10, products = n/100 "
        f"(minimum 100 / 50)",
        "Method            : fresh database per size; first run = full load; second run = same "
        "files again (idempotent re-run)",
        "",
        "All timings are wall-clock seconds measured during this benchmark run.",
        "",
        f"{'order items':>12} {'orders':>8} {'generate':>9} {'extract':>8} {'validate':>9} "
        f"{'transform':>10} {'load':>8} {'reconcile':>10} {'total':>8} {'rows/s':>9} "
        f"{'re-run':>8} {'re-run ins/unch':>16}",
        "-" * 125,
    ]
    for r in rows:
        s = r.stage_s
        lines.append(
            f"{r.order_items:>12,} {r.orders:>8,} {r.generate_s:>9.3f} {s['extract']:>8.3f} "
            f"{s['validate']:>9.3f} {s['transform']:>10.3f} {s['load']:>8.3f} "
            f"{s['reconcile']:>10.3f} {r.total_s:>8.3f} {r.rows_per_second:>9,.0f} "
            f"{r.rerun_s:>8.3f} {f'{r.rerun_inserted}/{r.rerun_unchanged}':>16}")
    lines += [
        "",
        "Column notes: 'total' is the end-to-end pipeline time (excludes data generation);",
        "'load' = raw landing + quarantine + source upserts + dimensions + date dim + facts;",
        "'transform' = typing/cleaning + fact build + point-in-time key resolution;",
        "'rows/s' = order-item rows / total; 're-run ins/unch' must be 0/<all> (idempotency).",
        "Run status (first/second): " + ", ".join(f"{r.order_items:,}={r.status}" for r in rows),
    ]
    if experiment:
        n, insert_s, copy_s = experiment
        lines += [
            "",
            "Optimisation experiment - bulk loading strategy (same fact rows, temp table):",
            f"  row-by-row INSERT (one statement per row): {n:,} rows in {insert_s:.3f}s "
            f"({n / insert_s:,.0f} rows/s)",
            f"  PostgreSQL COPY (used by the pipeline)   : {n:,} rows in {copy_s:.3f}s "
            f"({n / copy_s:,.0f} rows/s)",
            f"  speed-up: {insert_s / copy_s:,.1f}x",
            "",
            "Chosen optimisation: every bulk write in the pipeline uses COPY into a temporary",
            "staging table followed by ONE set-based INSERT ... ON CONFLICT statement, instead of",
            "per-row INSERT/UPDATE round trips. Validation and measures are vectorised in pandas;",
            "Pydantic validates rows in chunks of BATCH_SIZE (one TypeAdapter call per chunk).",
        ]
    if rows:
        largest = rows[-1]
        slowest = max(largest.stage_s, key=lambda k: largest.stage_s[k])
        share = 100 * largest.stage_s[slowest] / largest.total_s if largest.total_s else 0
        lines += [
            "",
            f"Measured bottleneck at {largest.order_items:,} rows: '{slowest}' "
            f"({largest.stage_s[slowest]:.2f}s, {share:.0f}% of the total).",
            "Next candidates (not implemented): replace the per-row Pydantic pass with fully",
            "vectorised checks for very large files; partition raw.* by run for 10M+ rows; drop and",
            "rebuild fact indexes around very large initial loads.",
        ]
    return "\n".join(lines) + "\n"
