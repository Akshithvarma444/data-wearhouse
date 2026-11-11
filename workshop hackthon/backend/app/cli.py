"""Command-line interface.

    python -m app.cli init-db [--reset --yes]
    python -m app.cli run --input ../other/datasets/sample [--run-id ID] [--batch-size N]
                          [--strict-schema] [--from-date YYYY-MM-DD --to-date YYYY-MM-DD]
    python -m app.cli reconcile [--run-id ID]
    python -m app.cli queries [--name monthly_revenue] [--limit 20]
    python -m app.cli runs [--limit 10]
    python -m app.cli benchmark [--sizes 1000,10000,100000] [--include-1m]
    python -m app.cli demo-scenarios
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from app.config.settings import ConfigurationError, get_settings
from app.database.connection import database_name, get_engine, init_db
from app.services.monitoring.logger import setup_logging


def _cmd_init_db(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.reset and not args.yes:
        print(f"--reset drops schemas raw, source, dw, ops in database "
              f"'{database_name(settings.database_url)}'. Re-run with --yes to confirm.")
        return 2
    engine = get_engine(settings.database_url)
    try:
        init_db(engine, reset=args.reset)
    finally:
        engine.dispose()
    print(f"Database '{database_name(settings.database_url)}' initialised "
          f"(schemas raw, source, dw, ops).")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from app.services.pipeline import print_summary, run_pipeline

    settings = get_settings()
    result = run_pipeline(args.input, settings=settings, run_id=args.run_id,
                          batch_size=args.batch_size, strict_schema=args.strict_schema,
                          from_date=args.from_date, to_date=args.to_date)
    print_summary(result)  # logged to console and logs/pipeline.log
    if result.reconciliation is not None and result.reconciliation.report_path:
        print(f"Reconciliation report: {result.reconciliation.report_path}")
    return 0 if result.succeeded else 1


def _cmd_reconcile(args: argparse.Namespace) -> int:
    from app.services.reconciliation.reconcile import (format_reconciliation_summary, latest_run_id,
                                              reconcile_run)

    settings = get_settings()
    engine = get_engine(settings.database_url)
    try:
        run_id = args.run_id or latest_run_id(engine)
        if run_id is None:
            print("No pipeline runs found. Run the pipeline first.")
            return 1
        result = reconcile_run(engine, run_id, settings.reports_dir, settings.source_system)
    finally:
        engine.dispose()
    print(format_reconciliation_summary(result))
    print(f"Report: {result.report_path}")
    return 0 if result.passed else 1


def _cmd_queries(args: argparse.Namespace) -> int:
    from app.services.analytics.queries import load_queries, run_query

    settings = get_settings()
    queries = load_queries()
    names = [args.name] if args.name else list(queries)
    engine = get_engine(settings.database_url)
    try:
        with pd.option_context("display.max_columns", 20, "display.width", 200):
            for name in names:
                frame = run_query(engine, name, queries)
                print(f"\n### {name} ({len(frame)} row(s))")
                print(frame.head(args.limit).to_string(index=False) if not frame.empty
                      else "(no rows)")
    finally:
        engine.dispose()
    return 0


def _cmd_runs(args: argparse.Namespace) -> int:
    from app.services.pipeline import latest_runs

    engine = get_engine(get_settings().database_url)
    try:
        rows = latest_runs(engine, args.limit)
    finally:
        engine.dispose()
    if not rows:
        print("No runs recorded.")
        return 0
    with pd.option_context("display.width", 220, "display.max_colwidth", 60):
        print(pd.DataFrame(rows).to_string(index=False))
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    from app.services.benchmark import run_benchmark

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    if args.include_1m and 1_000_000 not in sizes:
        sizes.append(1_000_000)
    report = run_benchmark(sizes, seed=args.seed, reuse_data=args.reuse_data)
    print(report.read_text(encoding="utf-8"))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from app.services.scenarios import run_scenario_demo

    path = run_scenario_demo()
    print(path.read_text(encoding="utf-8"))
    return 0


def _cmd_quality_report(args: argparse.Namespace) -> int:
    from app.services.reporting import write_quality_report

    settings = get_settings()
    engine = get_engine(settings.database_url)
    try:
        path = write_quality_report(engine, settings.reports_dir)
    finally:
        engine.dispose()
    print(f"Data quality report written to {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli",
                                     description="E-Commerce Sales Data Warehouse pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create schemas, tables and indexes")
    p.add_argument("--reset", action="store_true", help="drop and recreate all managed schemas")
    p.add_argument("--yes", action="store_true", help="confirm --reset")
    p.set_defaults(func=_cmd_init_db)

    p = sub.add_parser("run", help="run the ETL pipeline")
    p.add_argument("--input", type=Path, default=None,
                   help="directory with the 4 CSV files (default: SOURCE_DATA_DIR or other/datasets/generated)")
    p.add_argument("--run-id", default=None, help="explicit run identifier (must be unique)")
    p.add_argument("--batch-size", type=int, default=None, help="validation chunk size")
    p.add_argument("--strict-schema", action="store_true",
                   help="fail the run on unexpected columns instead of warning")
    p.add_argument("--from-date", type=date.fromisoformat, default=None,
                   help="backfill: only load orders on/after this date")
    p.add_argument("--to-date", type=date.fromisoformat, default=None,
                   help="backfill: only load orders on/before this date")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("reconcile", help="reconcile a run (default: latest)")
    p.add_argument("--run-id", default=None)
    p.set_defaults(func=_cmd_reconcile)

    p = sub.add_parser("queries", help="run analytical queries")
    p.add_argument("--name", default=None, help="query name (default: all)")
    p.add_argument("--limit", type=int, default=15, help="rows to display per query")
    p.set_defaults(func=_cmd_queries)

    p = sub.add_parser("runs", help="list recent pipeline runs")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=_cmd_runs)

    p = sub.add_parser("benchmark", help="run the 1K/10K/100K performance benchmark")
    p.add_argument("--sizes", default="1000,10000,100000", help="comma-separated order-item counts")
    p.add_argument("--include-1m", action="store_true", help="also run 1,000,000 order items")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reuse-data", action="store_true",
                   help="reuse previously generated benchmark CSVs if present")
    p.set_defaults(func=_cmd_benchmark)

    p = sub.add_parser("quality-report", help="write reports/data_quality_report.csv")
    p.set_defaults(func=_cmd_quality_report)

    p = sub.add_parser("demo-scenarios",
                       help="run SCD2 / late-arriving / missing-reference scenarios (demo DB)")
    p.set_defaults(func=_cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(settings.log_level, settings.logs_dir)
    if getattr(args, "input", "unset") is None:
        args.input = settings.source_data_dir
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
