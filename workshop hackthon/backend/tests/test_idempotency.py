"""Idempotency tests: re-runs, corrections, order changes, run identifiers."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.services.monitoring.metrics import DuplicateRunIdError
from app.services.pipeline import run_pipeline
from app.services.scenarios import item, order, write_batch
from tests.conftest import SAMPLE_DIR, scalar

TOTALS_SQL = ("SELECT count(*) || '|' || sum(quantity) || '|' || sum(gross_sales) || '|' || "
              "sum(discount_amount) || '|' || sum(net_revenue) FROM dw.fact_sales")


def snapshot(engine: Engine) -> dict[str, object]:
    return {
        "facts": scalar(engine, TOTALS_SQL),
        "dim_customer": scalar(engine, "SELECT count(*) FROM dw.dim_customer"),
        "dim_product": scalar(engine, "SELECT count(*) FROM dw.dim_product"),
        "dim_date": scalar(engine, "SELECT count(*) FROM dw.dim_date"),
        "source_items": scalar(engine, "SELECT count(*) FROM source.order_items"),
    }


def test_same_input_twice_changes_nothing(db: Engine, small_batch: Path, reports_dir: Path) -> None:
    first = run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    before = snapshot(db)
    second = run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    assert first.status == second.status == "SUCCESS"
    assert first.summary.counts.inserted_count == 3
    c = second.summary.counts
    assert (c.inserted_count, c.updated_count, c.unchanged_count) == (0, 0, 3)
    assert snapshot(db) == before
    assert second.reconciliation is not None and second.reconciliation.passed


def test_sample_dataset_three_runs_keep_totals(db: Engine, reports_dir: Path) -> None:
    results = [run_pipeline(SAMPLE_DIR, engine=db, reports_dir=reports_dir) for _ in range(3)]
    assert [r.status for r in results] == ["SUCCESS"] * 3
    assert [r.summary.counts.inserted_count for r in results] == [100, 0, 0]
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 100
    assert scalar(db, "SELECT count(*) FROM ops.pipeline_runs WHERE status = 'SUCCESS'") == 3


def test_corrected_source_record_updates_fact_in_place(db: Engine, small_batch: Path,
                                                       reports_dir: Path, tmp_path: Path) -> None:
    run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    # Late correction: OI1 quantity 2 -> 3 (order, customer, product already loaded).
    correction = write_batch(tmp_path / "correction", order_items=[
        item("OI1", "O1", "P1", 3, "1000.00", "100.00", "2024-03-10 11:00:00")])
    result = run_pipeline(correction, engine=db, reports_dir=reports_dir)
    assert result.status == "SUCCESS"
    assert (result.summary.counts.inserted_count, result.summary.counts.updated_count) == (0, 1)
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 3
    assert scalar(db, "SELECT gross_sales FROM dw.fact_sales WHERE order_item_id = 'OI1'") \
        == Decimal("3000.00")
    assert scalar(db, "SELECT run_id FROM dw.fact_sales WHERE order_item_id = 'OI1'") \
        == result.run_id


def test_order_status_change_updates_every_line(db: Engine, small_batch: Path,
                                                reports_dir: Path, tmp_path: Path) -> None:
    run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    returned = {**order("O1", "C1", "2024-03-10 11:00:00", status="RETURNED"),
                "updated_at": "2024-03-20 09:00:00"}
    result = run_pipeline(write_batch(tmp_path / "status", orders=[returned]), engine=db,
                          reports_dir=reports_dir)
    assert result.status == "SUCCESS"
    assert result.metrics["fact_lines_rebuilt_from_changed_orders"] == 2
    assert result.summary.counts.updated_count == 2
    assert scalar(db, "SELECT string_agg(DISTINCT order_status, ',') FROM dw.fact_sales "
                      "WHERE order_id = 'O1'") == "RETURNED"
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 3


def test_stale_order_update_is_ignored(db: Engine, small_batch: Path, reports_dir: Path,
                                       tmp_path: Path) -> None:
    run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    newer = {**order("O1", "C1", "2024-03-10 11:00:00", status="RETURNED"),
             "updated_at": "2024-04-01 00:00:00"}
    older = {**order("O1", "C1", "2024-03-10 11:00:00", status="SHIPPED"),
             "updated_at": "2024-03-15 00:00:00"}
    run_pipeline(write_batch(tmp_path / "newer", orders=[newer]), engine=db, reports_dir=reports_dir)
    result = run_pipeline(write_batch(tmp_path / "older", orders=[older]), engine=db,
                          reports_dir=reports_dir)
    assert result.status == "SUCCESS"
    assert result.summary.counts.updated_count == 0
    assert scalar(db, "SELECT order_status FROM source.orders WHERE order_id = 'O1'") == "RETURNED"


def test_run_id_cannot_be_reused(db: Engine, small_batch: Path, reports_dir: Path) -> None:
    run_pipeline(small_batch, engine=db, run_id="manual-run-1", reports_dir=reports_dir)
    with pytest.raises(DuplicateRunIdError):
        run_pipeline(small_batch, engine=db, run_id="manual-run-1", reports_dir=reports_dir)
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 3


def test_stale_order_header_with_redelivered_lines(db: Engine, small_batch: Path,
                                                   reports_dir: Path, tmp_path: Path) -> None:
    """Regression: an order header older than the stored one is ignored by the source
    upsert; its re-delivered lines must still be linked to the stored (newer) header,
    not to the stale copy in the batch."""
    run_pipeline(small_batch, engine=db, reports_dir=reports_dir)
    newer = {**order("O1", "C1", "2024-03-10 11:00:00", status="RETURNED"),
             "updated_at": "2024-04-01 00:00:00"}
    run_pipeline(write_batch(tmp_path / "newer", orders=[newer]), engine=db, reports_dir=reports_dir)
    stale = {**order("O1", "C2", "2024-03-10 11:00:00", status="SHIPPED"),
             "updated_at": "2024-03-15 00:00:00"}
    lines = [item("OI1", "O1", "P1", 2, "1000.00", "100.00", "2024-03-10 11:00:00"),
             item("OI2", "O1", "P2", 3, "250.50", "0.00", "2024-03-10 11:00:00")]
    result = run_pipeline(write_batch(tmp_path / "stale", orders=[stale], order_items=lines),
                          engine=db, reports_dir=reports_dir)
    assert result.status == "SUCCESS", result.summary.error_message
    assert scalar(db, "SELECT string_agg(DISTINCT c.customer_id || '/' || f.order_status, ',') "
                      "FROM dw.fact_sales f JOIN dw.dim_customer c USING (customer_key) "
                      "WHERE f.order_id = 'O1'") == "C1/RETURNED"
