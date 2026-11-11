"""End-to-end pipeline tests: success, empty, invalid, failures, retries, scenarios, drift."""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

import app.services.pipeline as pipeline_module
from app.generators.generate_data import generate_invalid_dataset
from app.services.pipeline import run_pipeline
from app.services.scenarios import build_scenario_batches, product, write_batch
from app.services.validation.schemas import ReasonCode
from tests.conftest import SAMPLE_DIR, scalar


def run(directory: Path, engine: Engine, reports_dir: Path, **kwargs: object):
    return run_pipeline(directory, engine=engine, reports_dir=reports_dir, **kwargs)


def fact_keys(engine: Engine) -> dict[str, tuple[int, str, int, str, int]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT f.order_item_id, f.customer_key, c.city, f.product_key, p.category, f.date_key
            FROM dw.fact_sales f
            JOIN dw.dim_customer c ON c.customer_key = f.customer_key
            JOIN dw.dim_product p ON p.product_key = f.product_key""")).all()
    return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in rows}


def test_complete_successful_pipeline(db: Engine, reports_dir: Path) -> None:
    result = run(SAMPLE_DIR, db, reports_dir)
    assert result.status == "SUCCESS"
    c = result.summary.counts
    assert (c.input_count, c.valid_count, c.rejected_count, c.inserted_count) == (208, 208, 0, 100)
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 100
    assert scalar(db, "SELECT count(*) FROM dw.dim_customer") == 40
    assert scalar(db, "SELECT count(*) FROM dw.dim_product") == 25
    assert scalar(db, "SELECT count(*) FROM dw.dim_date") == 1096  # 2023-01-01..2025-12-31
    with db.connect() as conn:
        row = conn.execute(text("SELECT * FROM ops.pipeline_runs WHERE run_id = :r"),
                           {"r": result.run_id}).one()
    assert (row.status, row.input_count, row.valid_count, row.inserted_count) == \
        ("SUCCESS", 208, 208, 100)
    assert row.end_time is not None and row.execution_duration_seconds > 0
    assert scalar(db, "SELECT metric_value FROM ops.pipeline_metrics WHERE run_id = :r "
                      "AND metric_name = 'facts_inserted'", r=result.run_id) == Decimal("100")
    assert scalar(db, "SELECT count(*) FROM ops.source_files WHERE run_id = :r",
                  r=result.run_id) == 4
    assert result.reconciliation is not None and result.reconciliation.passed
    assert (reports_dir / "reconciliation_report.csv").exists()


def test_empty_batch(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    result = run(write_batch(tmp_path / "empty"), db, reports_dir)
    assert result.status == "SUCCESS"
    assert result.summary.counts.input_count == 0
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 0
    assert scalar(db, "SELECT status FROM ops.pipeline_runs") == "SUCCESS"


def test_partial_invalid_batch(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    manifest = generate_invalid_dataset(tmp_path / "invalid")
    result = run(tmp_path / "invalid", db, reports_dir)
    assert result.status == "SUCCESS"
    c = result.summary.counts
    assert c.input_count == c.valid_count + c.rejected_count
    assert c.rejected_count == 25 and c.inserted_count == 60
    assert scalar(db, "SELECT count(*) FROM ops.rejected_records") == 25
    with db.connect() as conn:
        rejects = conn.execute(text("""SELECT entity_name, source_record_id, reason_codes,
                                              raw_record, source_file, source_row_number
                                       FROM ops.rejected_records""")).all()
    assert all(r.raw_record and r.source_file.endswith(".csv") and r.source_row_number > 1
               for r in rejects)
    found = {(r.entity_name, r.source_record_id or "", code) for r in rejects
             for code in r.reason_codes}
    for row in manifest.fillna("").itertuples():
        assert (row.entity, row.source_record_id, row.expected_reason_code) in found
    # nothing rejected reached the fact table
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales WHERE order_item_id LIKE 'OI9%'") == 0
    assert scalar(db, "SELECT count(*) FROM raw.order_items WHERE validation_status = "
                      "'REJECTED'") == 13


def _fail_fact_load(*args: object, **kwargs: object) -> tuple[int, int, int]:
    raise ConnectionError("simulated database failure during fact load")


def test_database_failure_rolls_back_warehouse(db: Engine, reports_dir: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    generate_invalid_dataset(tmp_path / "invalid")
    monkeypatch.setattr(pipeline_module, "upsert_fact_sales", _fail_fact_load)
    result = run(tmp_path / "invalid", db, reports_dir)
    assert result.status == "FAILED"
    assert "simulated database failure" in (result.summary.error_message or "")
    with db.connect() as conn:
        run_row = conn.execute(text("SELECT status, error_message, failed_count "
                                    "FROM ops.pipeline_runs")).one()
    assert run_row.status == "FAILED" and "ConnectionError" in run_row.error_message
    assert run_row.failed_count == result.summary.counts.valid_count
    # warehouse transaction rolled back completely (source, dims, facts)
    for table in ("source.customers", "source.order_items", "dw.dim_customer", "dw.dim_product",
                  "dw.dim_date", "dw.fact_sales"):
        assert scalar(db, f"SELECT count(*) FROM {table}") == 0, table
    # ...but raw records, quarantine and metrics survive for investigation
    assert scalar(db, "SELECT count(*) FROM raw.order_items") == 73
    assert scalar(db, "SELECT count(*) FROM ops.rejected_records") == 25
    assert scalar(db, "SELECT count(*) FROM ops.pipeline_metrics") > 0


def test_retry_after_failure(db: Engine, reports_dir: Path, tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(pipeline_module, "upsert_fact_sales", _fail_fact_load)
        failed = run(SAMPLE_DIR, db, reports_dir)
    assert failed.status == "FAILED"
    retried = run(SAMPLE_DIR, db, reports_dir)
    assert retried.status == "SUCCESS"
    assert retried.summary.counts.inserted_count == 100
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 100
    assert retried.reconciliation is not None and retried.reconciliation.passed
    statuses = scalar(db, "SELECT string_agg(status, ',' ORDER BY start_time) FROM ops.pipeline_runs")
    assert statuses == "FAILED,SUCCESS"


def test_scd2_customer_move(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    """Scenario 1: Hyderabad -> Chennai. Old sales keep the old version."""
    batches = build_scenario_batches(tmp_path)
    run(batches["batch1_initial"], db, reports_dir)
    result = run(batches["batch2_customer_moves"], db, reports_dir)
    assert result.status == "SUCCESS"
    assert result.metrics["dim_customer_new_versions"] == 1
    with db.connect() as conn:
        versions = conn.execute(text("""SELECT customer_key, city, is_current, effective_end_date
                                        FROM dw.dim_customer WHERE customer_id = 'C1'
                                        ORDER BY effective_start_date""")).all()
    assert [(v.city, v.is_current) for v in versions] == [("Hyderabad", False), ("Chennai", True)]
    keys = fact_keys(db)
    assert keys["OI1"][0] == versions[0].customer_key and keys["OI1"][1] == "Hyderabad"
    assert keys["OI3"][0] == versions[1].customer_key and keys["OI3"][1] == "Chennai"


def test_late_arriving_order(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    """Scenario 2: an April order arrives after the June move -> historical versions."""
    batches = build_scenario_batches(tmp_path)
    for name in ("batch1_initial", "batch2_customer_moves", "batch3_late_arrivals"):
        assert run(batches[name], db, reports_dir).status == "SUCCESS"
    keys = fact_keys(db)
    assert keys["OI4"][1] == "Hyderabad"          # customer version valid on 2024-04-20
    assert keys["OI4"][3] == "Electronics"        # product version valid on 2024-04-20
    assert keys["OI4"][4] == 20240420             # correct historical date key
    assert keys["OI4"][0] == keys["OI1"][0]
    assert keys["OI3"][1:4:2] == ("Chennai", "Mobiles")
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 4  # OI3 re-delivered, not duplicated


def test_missing_product_reference_is_quarantined(db: Engine, reports_dir: Path,
                                                  tmp_path: Path) -> None:
    """Scenario 3: unknown product -> quarantine, no broken foreign key."""
    batches = build_scenario_batches(tmp_path)
    run(batches["batch1_initial"], db, reports_dir)
    result = run(batches["batch4_missing_product"], db, reports_dir)
    assert result.status == "SUCCESS"
    with db.connect() as conn:
        reject = conn.execute(text("SELECT * FROM ops.rejected_records")).one()
    assert reject.source_record_id == "OI5"
    assert reject.reason_codes == [ReasonCode.UNKNOWN_PRODUCT_REFERENCE]
    assert "P999" in reject.rejection_reason
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales WHERE order_item_id = 'OI5'") == 0
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales WHERE order_item_id = 'OI6'") == 1


def test_late_dimension_change_rekeys_existing_facts(db: Engine, reports_dir: Path,
                                                     tmp_path: Path) -> None:
    """Domain edge case: a product re-categorisation effective in May arrives after the July
    sale was loaded; the existing fact is moved to the correct version without re-delivery."""
    batches = build_scenario_batches(tmp_path)
    run(batches["batch1_initial"], db, reports_dir)
    run(batches["batch2_customer_moves"], db, reports_dir)
    late_change = write_batch(tmp_path / "late_product", products=[
        product("P1", "Mobiles", "2024-05-01 00:00:00")])
    result = run(late_change, db, reports_dir)
    assert result.status == "SUCCESS"
    assert result.metrics["facts_rekeyed"] == 1
    keys = fact_keys(db)
    assert keys["OI1"][3] == "Electronics"  # March sale stays on the old version
    assert keys["OI3"][3] == "Mobiles"      # July sale re-keyed
    assert result.reconciliation is not None and result.reconciliation.passed


def _copy_sample(tmp_path: Path) -> Path:
    target = tmp_path / "sample_copy"
    shutil.copytree(SAMPLE_DIR, target)
    return target


def _add_column(csv_path: Path, name: str, value: str) -> None:
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    lines = [lines[0] + f",{name}"] + [line + f",{value}" for line in lines[1:]]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_unexpected_schema_change_is_tolerated_and_recorded(db: Engine, reports_dir: Path,
                                                            tmp_path: Path) -> None:
    source = _copy_sample(tmp_path)
    _add_column(source / "orders.csv", "coupon_code", "DIWALI10")
    result = run(source, db, reports_dir)
    assert result.status == "SUCCESS"
    assert result.metrics["schema_drift_columns_orders"] == 1
    assert scalar(db, "SELECT count(*) FROM raw.orders WHERE extra_attributes->>'coupon_code' "
                      "= 'DIWALI10'") == 43


def test_unexpected_schema_change_fails_in_strict_mode(db: Engine, reports_dir: Path,
                                                       tmp_path: Path) -> None:
    source = _copy_sample(tmp_path)
    _add_column(source / "orders.csv", "coupon_code", "X")
    result = run(source, db, reports_dir, strict_schema=True)
    assert result.status == "FAILED"
    assert "SchemaDriftError" in (result.summary.error_message or "")
    assert scalar(db, "SELECT count(*) FROM raw.orders") == 0


def test_missing_required_column_fails_run(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    source = _copy_sample(tmp_path)
    lines = (source / "products.csv").read_text(encoding="utf-8").splitlines()
    (source / "products.csv").write_text(
        "\n".join(",".join(line.split(",")[:4] + line.split(",")[5:]) for line in lines) + "\n",
        encoding="utf-8")  # drop unit_price
    result = run(source, db, reports_dir)
    assert result.status == "FAILED"
    assert "unit_price" in (result.summary.error_message or "")
    assert scalar(db, "SELECT status FROM ops.pipeline_runs") == "FAILED"


def test_missing_file_fails_run(db: Engine, reports_dir: Path, tmp_path: Path) -> None:
    source = _copy_sample(tmp_path)
    (source / "orders.csv").unlink()
    result = run(source, db, reports_dir)
    assert result.status == "FAILED"
    assert "SourceFileNotFoundError" in (result.summary.error_message or "")


def test_backfill_window(db: Engine, reports_dir: Path) -> None:
    result = run(SAMPLE_DIR, db, reports_dir, from_date=date(2024, 1, 1), to_date=date(2024, 12, 31))
    assert result.status == "SUCCESS"
    c = result.summary.counts
    assert c.skipped_count > 0
    assert c.input_count == c.valid_count + c.rejected_count + c.skipped_count
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales WHERE date_key NOT BETWEEN "
                      "20240101 AND 20241231") == 0
    # loading the rest later completes the warehouse without duplicates
    rest = run(SAMPLE_DIR, db, reports_dir)
    assert rest.summary.counts.inserted_count == 100 - result.summary.counts.inserted_count
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 100
