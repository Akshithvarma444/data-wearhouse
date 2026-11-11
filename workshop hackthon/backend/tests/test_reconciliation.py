"""Reconciliation tests: passing runs, detection of tampering, report output."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, text

import app.services.pipeline as pipeline_module
from app.generators.generate_data import generate_invalid_dataset
from app.services.loading.load import upsert_fact_sales
from app.services.pipeline import run_pipeline
from app.services.reconciliation.reconcile import reconcile_run
from app.services.scenarios import build_scenario_batches


def test_successful_run_reconciles_and_writes_report(db: Engine, small_batch: Path,
                                                     reports_dir: Path) -> None:
    result = run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    assert result.reconciliation is not None and result.reconciliation.passed
    with (reports_dir / "reconciliation_report.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert set(rows[0]) == {"run_id", "check_name", "severity", "status", "source_value",
                            "target_value", "difference", "explanation"}
    assert {r["status"] for r in rows if r["severity"] == "CRITICAL"} == {"PASS"}
    totals = {r["check_name"]: r for r in rows}
    net = totals["total net revenue (valid source vs fact)"]
    # OI1 2x1000.00-100.00 + OI2 3x250.50 + OI3 250.50-25.05 = 1900.00 + 751.50 + 225.45
    assert net["source_value"] == net["target_value"] == "2876.95"
    assert (reports_dir / "reconciliation_summary.txt").read_text(encoding="utf-8").count("PASSED")
    assert (reports_dir / "reconciliation" / f"reconciliation_{result.run_id}.csv").exists()


def test_input_equals_valid_plus_rejected(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    generate_invalid_dataset(tmp_path / "invalid")
    result = run_pipeline(tmp_path / "invalid", engine=db, reports_dir=reports_dir)
    assert result.reconciliation is not None
    checks = {c.check_name: c for c in result.reconciliation.checks}
    check = checks["order_items: input = valid + rejected + skipped"]
    assert (check.source_value, check.target_value, check.status) == (73, 73, "PASS")
    assert checks["valid source order items = fact rows present"].target_value == 60
    assert checks["duplicate records quarantined (all entities)"].source_value == 6


def test_missing_fact_row_is_detected(db: Engine, small_batch: Path, reports_dir: Path) -> None:
    result = run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    with db.begin() as conn:
        conn.execute(text("DELETE FROM dw.fact_sales WHERE order_item_id = 'OI2'"))
    recon = reconcile_run(db, result.run_id, reports_dir)
    assert not recon.passed
    failed = {c.check_name for c in recon.failed_checks}
    assert "valid source order items = fact rows present" in failed
    assert "total net revenue (valid source vs fact)" in failed
    assert "warehouse total: source order items = fact rows" in failed


def test_wrong_dimension_version_is_detected(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    batches = build_scenario_batches(tmp_path)
    run_pipeline(batches["batch1_initial"], engine=db, reports_dir=reports_dir)
    result = run_pipeline(batches["batch2_customer_moves"], engine=db, reports_dir=reports_dir)
    with db.begin() as conn:  # corrupt: point the July sale at the expired Hyderabad version
        conn.execute(text("""UPDATE dw.fact_sales SET customer_key =
                               (SELECT customer_key FROM dw.dim_customer
                                WHERE customer_id = 'C1' AND NOT is_current)
                             WHERE order_item_id = 'OI3'"""))
    recon = reconcile_run(db, result.run_id, reports_dir)
    assert [c.check_name for c in recon.failed_checks] == [
        "facts linked to wrong dimension version / date"]


def test_pipeline_does_not_report_success_when_reconciliation_fails(
        db: Engine, small_batch: Path, reports_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def drop_one_fact(conn, facts: pd.DataFrame, run_id: str, source_system: str):
        return upsert_fact_sales(conn, facts.iloc[:-1], run_id, source_system)

    monkeypatch.setattr(pipeline_module, "upsert_fact_sales", drop_one_fact)
    result = run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    assert result.status == "RECONCILIATION_FAILED"
    assert "valid source order items" in (result.summary.error_message or "")
    with db.connect() as conn:
        status = conn.execute(text("SELECT status FROM ops.pipeline_runs")).scalar()
    assert status == "RECONCILIATION_FAILED"


def test_unknown_run_raises(db: Engine) -> None:
    with pytest.raises(ValueError, match="not found"):
        reconcile_run(db, "no-such-run")
