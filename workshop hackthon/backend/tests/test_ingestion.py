"""Ingestion tests: file checks, header checks, schema drift, malformed rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ingestion.extract import (EmptySourceFileError, InvalidFileExtensionError,
                                   SchemaDriftError, SchemaMismatchError,
                                   SourceFileNotFoundError, extract_all, extract_customers,
                                   extract_entity, extract_order_items, extract_orders)
from app.services.validation.schemas import ENTITY_SPECS
from tests.conftest import SAMPLE_DIR

HEADER = "order_id,customer_id,order_date,order_status,payment_method,updated_at\n"
ROW = "O1,C1,2024-01-01 10:00:00,PLACED,UPI,2024-01-01 10:00:00\n"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_csv_is_extracted_completely() -> None:
    entities = extract_all(SAMPLE_DIR)
    assert set(entities) == {"customers", "products", "orders", "order_items"}
    items = entities["order_items"]
    assert items.record_count == 100
    assert items.source_file == "order_items.csv"
    assert len(items.file_sha256) == 64 and items.file_size_bytes > 0
    assert items.ingested_at.tzinfo is not None
    assert list(items.data.columns[:7]) == list(ENTITY_SPECS["order_items"].columns)
    # row numbers are physical line numbers (header is line 1)
    assert items.data["source_row_number"].tolist()[:3] == [2, 3, 4]
    # values are preserved as text, untouched
    assert items.data.loc[0, "unit_price"] == "849.00"


def test_missing_csv_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceFileNotFoundError):
        extract_orders(tmp_path)


def test_missing_source_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceFileNotFoundError):
        extract_all(tmp_path / "does_not_exist")


def test_empty_csv_file_raises(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", "")
    with pytest.raises(EmptySourceFileError):
        extract_orders(tmp_path)


def test_whitespace_only_file_raises(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", "\n\n  \n")
    with pytest.raises(EmptySourceFileError):
        extract_orders(tmp_path)


def test_header_only_file_is_a_valid_empty_batch(tmp_path: Path) -> None:
    entity = extract_orders(_write(tmp_path / "orders.csv", HEADER).parent)
    assert entity.record_count == 0
    assert entity.data.empty


def test_wrong_file_extension_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "orders.txt", HEADER + ROW)
    with pytest.raises(InvalidFileExtensionError):
        extract_entity(ENTITY_SPECS["orders"], file_path=path)


def test_missing_required_column_raises(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv",
           "order_id,order_date,order_status,payment_method,updated_at\n"
           "O1,2024-01-01 10:00:00,PLACED,UPI,2024-01-01 10:00:00\n")
    with pytest.raises(SchemaMismatchError, match="customer_id"):
        extract_orders(tmp_path)


def test_missing_optional_column_is_added_empty(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv",
           "order_id,customer_id,order_date,order_status\nO1,C1,2024-01-01 10:00:00,PLACED\n")
    entity = extract_orders(tmp_path)
    assert entity.missing_optional_columns == ["payment_method", "updated_at"]
    assert entity.data.loc[0, "payment_method"] == ""


def test_unexpected_column_is_reported_and_preserved(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", HEADER.rstrip("\n") + ",coupon_code\n" + ROW.rstrip("\n")
           + ",DIWALI10\n")
    entity = extract_orders(tmp_path)
    assert entity.unexpected_columns == ["coupon_code"]
    assert entity.data.loc[0, "coupon_code"] == "DIWALI10"


def test_unexpected_column_fails_in_strict_mode(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", HEADER.rstrip("\n") + ",coupon_code\n" + ROW.rstrip("\n")
           + ",X\n")
    with pytest.raises(SchemaDriftError):
        extract_orders(tmp_path, strict_schema=True)


def test_duplicate_header_names_raise(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", HEADER.rstrip("\n") + ",order_id\n")
    with pytest.raises(SchemaMismatchError, match="duplicate"):
        extract_orders(tmp_path)


def test_header_is_case_and_whitespace_insensitive(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", " ORDER_ID , Customer_Id ,order_date,order_status\n"
                                    "O1,C1,2024-01-01 10:00:00,PLACED\n")
    assert extract_orders(tmp_path).data.loc[0, "customer_id"] == "C1"


def test_malformed_row_is_captured_not_dropped(tmp_path: Path) -> None:
    _write(tmp_path / "order_items.csv",
           "order_item_id,order_id,product_id,quantity,unit_price,discount_amount,created_at\n"
           "OI1,O1,P1,1,10.00,0.00,2024-01-01 10:00:00\n"
           "OI2,O1,P1\n")
    entity = extract_order_items(tmp_path)
    assert entity.record_count == 2
    assert len(entity.data) == 1
    assert entity.malformed_rows[0].source_row_number == 3
    assert entity.malformed_rows[0].fields == ["OI2", "O1", "P1"]


def test_source_dir_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "customers.csv",
           "customer_id,first_name,last_name,email,city,state,country,created_at,updated_at\n")
    monkeypatch.setenv("SOURCE_DATA_DIR", str(tmp_path))
    assert extract_customers().file_path == (tmp_path / "customers.csv").resolve()
