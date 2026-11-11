"""Validation tests: schema rules, business rules, duplicates, references, quarantine."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.generators.generate_data import generate_invalid_dataset
from app.services.ingestion.extract import extract_all
from app.services.scenarios import customer, item, order, product, write_batch
from app.services.validation.schemas import ReasonCode
from app.services.validation.validate import (EntityValidationResult, InMemoryReferenceLookup,
                                     validate_all)
from tests.conftest import SAMPLE_DIR

C1 = customer("C1", "Hyderabad", "Telangana", "2024-01-01 10:00:00")
P1 = product("P1", "Electronics", "2024-01-01 10:00:00")
O1 = order("O1", "C1", "2024-03-10 11:00:00")
I1 = item("OI1", "O1", "P1", 2, "100.00", "10.00", "2024-03-10 11:00:00")


def run(tmp_path: Path, lookup: InMemoryReferenceLookup | None = None,
        **entities: list[dict[str, str]]) -> dict[str, EntityValidationResult]:
    batch = {"customers": [C1], "products": [P1], "orders": [O1], "order_items": [I1]}
    batch.update(entities)
    write_batch(tmp_path / "batch", **batch)
    return validate_all(extract_all(tmp_path / "batch"), lookup or InMemoryReferenceLookup())


def codes(result: EntityValidationResult) -> dict[str | None, list[str]]:
    return {r.source_record_id: r.reason_codes for r in result.rejected}


def test_valid_records_pass(tmp_path: Path) -> None:
    results = run(tmp_path)
    for result in results.values():
        assert result.rejected_count == 0
        assert result.valid_count == 1


def test_sample_dataset_is_fully_valid() -> None:
    results = validate_all(extract_all(SAMPLE_DIR), InMemoryReferenceLookup())
    assert sum(r.rejected_count for r in results.values()) == 0
    assert results["order_items"].valid_count == 100


def test_missing_customer_id(tmp_path: Path) -> None:
    results = run(tmp_path, customers=[C1, {**C1, "customer_id": " ", "email": "x@y.in"}])
    rejected = results["customers"].rejected
    assert len(rejected) == 1
    assert rejected[0].source_record_id is None
    assert rejected[0].reason_codes == [ReasonCode.MISSING_REQUIRED_FIELD]
    assert rejected[0].source_row_number == 3


def test_invalid_email(tmp_path: Path) -> None:
    results = run(tmp_path, customers=[C1, {**C1, "customer_id": "C2", "email": "no-at-sign"}])
    assert codes(results["customers"]) == {"C2": [ReasonCode.INVALID_EMAIL]}


def test_duplicate_customer_id_exact_keeps_first(tmp_path: Path) -> None:
    results = run(tmp_path, customers=[C1, C1])
    assert results["customers"].valid_count == 1
    rejected = results["customers"].rejected
    assert [r.reason_codes for r in rejected] == [[ReasonCode.EXACT_DUPLICATE]]
    assert rejected[0].source_row_number == 3  # the later copy is rejected
    assert results["orders"].valid_count == 1  # reference still satisfied


def test_duplicate_customer_id_conflicting_rejects_all_and_cascades(tmp_path: Path) -> None:
    moved = {**C1, "city": "Chennai", "state": "Tamil Nadu"}
    results = run(tmp_path, customers=[C1, moved])
    assert results["customers"].valid_count == 0
    assert all(r.reason_codes == [ReasonCode.CONFLICTING_DUPLICATE]
               for r in results["customers"].rejected)
    # dependent records cannot reference an unresolved customer
    assert codes(results["orders"]) == {"O1": [ReasonCode.UNKNOWN_CUSTOMER_REFERENCE]}
    assert codes(results["order_items"]) == {"OI1": [ReasonCode.UNKNOWN_ORDER_REFERENCE]}


def test_duplicate_order_item_id(tmp_path: Path) -> None:
    conflicting = {**I1, "quantity": "5"}
    results = run(tmp_path, order_items=[I1, conflicting])
    assert results["order_items"].valid_count == 0
    assert {tuple(r.reason_codes) for r in results["order_items"].rejected} == {
        (ReasonCode.CONFLICTING_DUPLICATE,)}


def test_negative_quantity(tmp_path: Path) -> None:
    results = run(tmp_path, order_items=[{**I1, "quantity": "-2"}])
    assert codes(results["order_items"])["OI1"][0] == ReasonCode.NON_POSITIVE_QUANTITY


def test_zero_and_non_numeric_quantity(tmp_path: Path) -> None:
    results = run(tmp_path, order_items=[{**I1, "quantity": "0", "discount_amount": "0"},
                                         {**I1, "order_item_id": "OI2", "quantity": "two"}])
    assert codes(results["order_items"]) == {"OI1": [ReasonCode.NON_POSITIVE_QUANTITY],
                                             "OI2": [ReasonCode.INVALID_NUMBER]}


def test_negative_price(tmp_path: Path) -> None:
    results = run(tmp_path, products=[{**P1, "unit_price": "-1.00"}],
                  order_items=[{**I1, "unit_price": "-5.00"}])
    assert codes(results["products"]) == {"P1": [ReasonCode.NEGATIVE_VALUE]}
    item_codes = codes(results["order_items"])["OI1"]
    assert ReasonCode.NEGATIVE_VALUE in item_codes
    assert ReasonCode.UNKNOWN_PRODUCT_REFERENCE in item_codes  # its product was rejected too


def test_excessive_discount(tmp_path: Path) -> None:
    results = run(tmp_path, order_items=[{**I1, "quantity": "2", "unit_price": "100.00",
                                          "discount_amount": "200.01"}])
    assert codes(results["order_items"]) == {"OI1": [ReasonCode.EXCESSIVE_DISCOUNT]}


def test_discount_equal_to_gross_is_allowed(tmp_path: Path) -> None:
    """Edge case: a 100% discount (free item) is legal; only discount > gross is rejected."""
    results = run(tmp_path, order_items=[{**I1, "quantity": "2", "unit_price": "100.00",
                                          "discount_amount": "200.00"}])
    assert results["order_items"].rejected_count == 0


def test_invalid_product_reference(tmp_path: Path) -> None:
    results = run(tmp_path, order_items=[{**I1, "product_id": "P404"}])
    rejected = results["order_items"].rejected[0]
    assert rejected.reason_codes == [ReasonCode.UNKNOWN_PRODUCT_REFERENCE]
    assert "P404" in rejected.rejection_reason


def test_invalid_customer_reference(tmp_path: Path) -> None:
    results = run(tmp_path, orders=[{**O1, "customer_id": "C404"}])
    assert codes(results["orders"]) == {"O1": [ReasonCode.UNKNOWN_CUSTOMER_REFERENCE]}
    assert codes(results["order_items"]) == {"OI1": [ReasonCode.UNKNOWN_ORDER_REFERENCE]}


def test_reference_satisfied_by_existing_warehouse_record(tmp_path: Path) -> None:
    """Late-arriving order for a customer loaded in an earlier run is valid."""
    lookup = InMemoryReferenceLookup(customer_ids={"C9"}, product_ids={"P9"}, order_ids={"O9"})
    results = run(tmp_path, lookup,
                  orders=[O1, {**O1, "order_id": "O2", "customer_id": "C9"}],
                  order_items=[I1, {**I1, "order_item_id": "OI2", "order_id": "O9",
                                    "product_id": "P9"}])
    assert results["orders"].rejected_count == 0
    assert results["order_items"].rejected_count == 0


def test_malformed_dates(tmp_path: Path) -> None:
    results = run(tmp_path,
                  orders=[{**O1, "order_date": "10/03/2024"}],
                  customers=[{**C1, "created_at": "2024-02-30 10:00:00"}])
    assert ReasonCode.INVALID_TIMESTAMP in codes(results["orders"])["O1"]
    assert codes(results["customers"])["C1"] == [ReasonCode.INVALID_TIMESTAMP]


def test_invalid_order_status_and_case_normalisation(tmp_path: Path) -> None:
    results = run(tmp_path, orders=[{**O1, "order_status": "delivered"},
                                    {**O1, "order_id": "O2", "order_status": "LOST"}])
    assert results["orders"].valid["order_status"].tolist() == ["DELIVERED"]
    assert codes(results["orders"]) == {"O2": [ReasonCode.INVALID_ALLOWED_VALUE]}


def test_multiple_reasons_for_one_record(tmp_path: Path) -> None:
    results = run(tmp_path, order_items=[{**I1, "quantity": "-1", "unit_price": "abc",
                                          "product_id": "P404"}])
    reasons = set(codes(results["order_items"])["OI1"])
    assert reasons == {ReasonCode.NON_POSITIVE_QUANTITY, ReasonCode.INVALID_NUMBER,
                       ReasonCode.UNKNOWN_PRODUCT_REFERENCE}
    assert results["order_items"].rejected[0].rejection_reason.count(";") == 2


def test_more_than_two_decimal_places_rejected(tmp_path: Path) -> None:
    results = run(tmp_path, order_items=[{**I1, "unit_price": "10.999"}])
    assert codes(results["order_items"]) == {"OI1": [ReasonCode.INVALID_DECIMAL_SCALE]}


def test_updated_before_created_rejected(tmp_path: Path) -> None:
    results = run(tmp_path, customers=[{**C1, "created_at": "2024-05-01 00:00:00",
                                        "updated_at": "2024-01-01 00:00:00"}])
    assert codes(results["customers"]) == {"C1": [ReasonCode.TIMESTAMP_ORDER]}


def test_email_shared_by_two_customers_rejected(tmp_path: Path) -> None:
    other = {**C1, "customer_id": "C2"}  # same e-mail, different customer
    results = run(tmp_path, customers=[C1, other])
    assert {r.source_record_id for r in results["customers"].rejected} == {"C1", "C2"}
    lookup = InMemoryReferenceLookup(email_owners={C1["email"]: "C77"})
    results = run(tmp_path / "second", lookup)
    assert codes(results["customers"]) == {"C1": [ReasonCode.DUPLICATE_EMAIL]}


def test_rejected_record_keeps_original_values(tmp_path: Path) -> None:
    results = run(tmp_path, products=[P1, {**P1, "product_id": "P2", "unit_price": " -9.50 "}])
    rejected = results["products"].rejected[0]
    assert rejected.raw_record["unit_price"] == " -9.50 "
    assert rejected.source_file == "products.csv"
    assert rejected.entity_name == "products"


def test_every_injected_defect_is_detected(tmp_path: Path) -> None:
    manifest = generate_invalid_dataset(tmp_path / "invalid", seed=7)
    results = validate_all(extract_all(tmp_path / "invalid"), InMemoryReferenceLookup())
    found = {(r.entity_name, r.source_record_id or "", code)
             for res in results.values() for r in res.rejected for code in r.reason_codes}
    expected = {(row.entity, row.source_record_id, row.expected_reason_code)
                for row in manifest.fillna("").itertuples()}
    assert expected <= found
    for result in results.values():  # input = valid + rejected for every entity
        assert result.input_count == result.valid_count + result.rejected_count
    assert isinstance(manifest, pd.DataFrame) and len(manifest) == 23


@pytest.mark.parametrize("value", ["1e2", "NaN", "1,000.00", "₹100"])
def test_non_plain_money_formats_rejected(tmp_path: Path, value: str) -> None:
    results = run(tmp_path, order_items=[{**I1, "unit_price": value}])
    assert codes(results["order_items"])["OI1"][0] == ReasonCode.INVALID_NUMBER
