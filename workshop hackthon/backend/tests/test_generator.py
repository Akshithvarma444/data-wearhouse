"""Synthetic data generator tests: determinism, volume control, valid relationships."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.generators.generate_data import (GeneratorConfig, generate_dataset,
                                          generate_invalid_dataset, main, write_dataset)
from app.services.ingestion.extract import extract_all
from app.services.validation.validate import InMemoryReferenceLookup, validate_all


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    config = GeneratorConfig(customers=50, products=20, orders=None, order_items=300, seed=11)
    write_dataset(generate_dataset(config), tmp_path / "a")
    write_dataset(generate_dataset(config), tmp_path / "b")
    for name in ("customers", "products", "orders", "order_items"):
        assert (tmp_path / "a" / f"{name}.csv").read_bytes() == \
            (tmp_path / "b" / f"{name}.csv").read_bytes()


def test_different_seed_gives_different_data() -> None:
    a = generate_dataset(GeneratorConfig(customers=30, products=10, orders=50, seed=1))
    b = generate_dataset(GeneratorConfig(customers=30, products=10, orders=50, seed=2))
    assert not a["order_items"].equals(b["order_items"])


def test_exact_order_item_count_and_valid_relationships() -> None:
    frames = generate_dataset(GeneratorConfig(customers=200, products=40, orders=None,
                                              order_items=1234, seed=5))
    items, orders = frames["order_items"], frames["orders"]
    assert len(items) == 1234
    assert items["order_item_id"].is_unique and orders["order_id"].is_unique
    assert set(items["order_id"]) <= set(orders["order_id"])
    assert set(items["product_id"]) <= set(frames["products"]["product_id"])
    assert set(orders["customer_id"]) <= set(frames["customers"]["customer_id"])
    assert frames["customers"]["email"].is_unique
    assert items.groupby("order_id").size().max() <= 6
    gross = items["quantity"].astype(int) * items["unit_price"].map(Decimal)
    assert (items["discount_amount"].map(Decimal) <= gross).all()
    assert items["unit_price"].str.fullmatch(r"\d+\.\d{2}").all()
    assert set(orders["order_status"]) <= {"PLACED", "SHIPPED", "DELIVERED", "CANCELLED",
                                           "RETURNED"}
    assert frames["customers"]["country"].eq("India").all()


def test_clean_dataset_passes_validation(tmp_path: Path) -> None:
    assert main(["--customers", "60", "--products", "25", "--orders", "150", "--seed", "3",
                 "--output-dir", str(tmp_path / "clean")]) == 0
    results = validate_all(extract_all(tmp_path / "clean"), InMemoryReferenceLookup())
    assert sum(r.rejected_count for r in results.values()) == 0


def test_invalid_dataset_is_kept_separate(tmp_path: Path) -> None:
    manifest = generate_invalid_dataset(tmp_path / "invalid")
    assert (tmp_path / "invalid" / "injected_defects.csv").exists()
    assert set(manifest.columns) == {"entity", "source_record_id", "defect",
                                     "expected_reason_code"}
    assert not (tmp_path / "clean").exists()
    items = pd.read_csv(tmp_path / "invalid" / "order_items.csv", dtype=str,
                        on_bad_lines="skip", keep_default_na=False)
    assert "OI900000005" in set(items["order_item_id"])
