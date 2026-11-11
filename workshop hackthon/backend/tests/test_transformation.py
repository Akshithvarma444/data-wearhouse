"""Transformation tests: measures, date keys, surrogate keys, point-in-time resolution."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.services.transformation.dimensions import (date_key_from_timestamp, date_range_for,
                                           generate_date_dimension, transform_customers,
                                           transform_products)
from app.services.transformation.facts import (KeyResolutionError, calculate_measures,
                                      resolve_surrogate_keys, transform_order_items,
                                      transform_orders, transform_sales)

D = Decimal


def _sales(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    """rows: (order_item_id, customer_id, product_id, order_timestamp)."""
    return pd.DataFrame({
        "order_item_id": [r[0] for r in rows],
        "customer_id": [r[1] for r in rows],
        "product_id": [r[2] for r in rows],
        "order_timestamp": pd.to_datetime([r[3] for r in rows]),
    })


def _versions(rows: list[tuple[int, str, str, str | None]]) -> pd.DataFrame:
    return pd.DataFrame({
        "key": [r[0] for r in rows], "business_id": [r[1] for r in rows],
        "effective_start_date": pd.to_datetime([r[2] for r in rows], format="ISO8601"),
        "effective_end_date": pd.to_datetime([r[3] for r in rows], format="ISO8601"),
    })


def test_gross_sales_and_net_revenue_are_exact() -> None:
    frame = pd.DataFrame({"quantity": [3, 1, 7],
                          "unit_price": [D("33.33"), D("0.10"), D("1499.99")],
                          "discount_amount": [D("0.99"), D("0.00"), D("1049.99")]})
    out = calculate_measures(frame)
    assert out["gross_sales"].tolist() == [D("99.99"), D("0.10"), D("10499.93")]
    assert out["net_revenue"].tolist() == [D("99.00"), D("0.10"), D("9449.94")]
    assert all(str(v).count(".") == 1 and len(str(v).split(".")[1]) == 2
               for v in out["net_revenue"])


def test_float_arithmetic_would_be_wrong_but_measures_are_not() -> None:
    """Domain edge case: 0.1 + 0.2 style drift must never reach revenue."""
    frame = pd.DataFrame({"quantity": [3] * 10, "unit_price": [D("0.10")] * 10,
                          "discount_amount": [D("0.00")] * 10})
    total = sum(calculate_measures(frame)["gross_sales"], D("0"))
    assert total == D("3.00")
    assert sum([0.1 * 3] * 10) != 3.0  # what float arithmetic would have produced


def test_calculate_measures_rejects_excessive_discount() -> None:
    frame = pd.DataFrame({"quantity": [1], "unit_price": [D("10.00")],
                          "discount_amount": [D("10.01")]})
    with pytest.raises(ValueError):
        calculate_measures(frame)


def test_date_key_generation() -> None:
    keys = date_key_from_timestamp(pd.Series(pd.to_datetime(
        ["2024-02-29 23:59:59", "2025-01-01 00:00:00", "2023-12-31 12:00:00"])))
    assert keys.tolist() == [20240229, 20250101, 20231231]


def test_date_dimension_attributes() -> None:
    dim = generate_date_dimension(date(2024, 1, 1), date(2024, 12, 31))
    assert len(dim) == 366  # leap year
    assert dim["date_key"].is_unique
    row = dim.loc[dim["date_key"] == 20240629].iloc[0]  # a Saturday
    assert (row["day"], row["month"], row["quarter"], row["year"]) == (29, 6, 2, 2024)
    assert row["day_name"] == "Saturday" and row["month_name"] == "June"
    assert bool(row["is_weekend"]) is True
    assert int(dim.loc[dim["date_key"] == 20241230, "week"].iloc[0]) == 1  # ISO week 1 of 2025
    with pytest.raises(ValueError):
        generate_date_dimension(date(2024, 2, 1), date(2024, 1, 1))


def test_date_range_covers_full_years() -> None:
    span = date_range_for(pd.Series(pd.to_datetime(["2024-05-03", "2025-02-01"])))
    assert span == (date(2024, 1, 1), date(2025, 12, 31))
    assert date_range_for(pd.Series([], dtype="datetime64[ns]")) is None


def test_surrogate_key_mapping() -> None:
    sales = _sales([("OI1", "C1", "P1", "2024-03-01 10:00:00"),
                    ("OI2", "C2", "P2", "2024-03-02 10:00:00")])
    customers = _versions([(10, "C1", "1900-01-01", None), (20, "C2", "1900-01-01", None)])
    products = _versions([(7, "P1", "1900-01-01", None), (8, "P2", "1900-01-01", None)])
    resolved, unresolved = resolve_surrogate_keys(sales, customers, products)
    assert unresolved.empty
    assert resolved.set_index("order_item_id")[["customer_key", "product_key"]].to_dict("index") \
        == {"OI1": {"customer_key": 10, "product_key": 7},
            "OI2": {"customer_key": 20, "product_key": 8}}


def test_historical_dimension_resolution_uses_event_date() -> None:
    """Old sales keep the old version; boundary instant belongs to the new version."""
    sales = _sales([("OI1", "C1", "P1", "2024-03-10 11:00:00"),
                    ("OI2", "C1", "P1", "2024-06-01 00:00:00"),   # exactly at the change
                    ("OI3", "C1", "P1", "2024-05-31 23:59:59"),
                    ("OI4", "C1", "P1", "2024-07-15 18:30:00")])
    customers = _versions([(1, "C1", "1900-01-01", "2024-06-01 00:00:00"),
                           (5, "C1", "2024-06-01 00:00:00", None)])
    products = _versions([(9, "P1", "1900-01-01", None)])
    resolved, _ = resolve_surrogate_keys(sales, customers, products)
    keys = dict(zip(resolved["order_item_id"], resolved["customer_key"]))
    assert keys == {"OI1": 1, "OI2": 5, "OI3": 1, "OI4": 5}


def test_unresolvable_keys_are_reported() -> None:
    sales = _sales([("OI1", "C1", "P1", "2024-03-10 11:00:00"),
                    ("OI2", "C404", "P1", "2024-03-10 11:00:00")])
    resolved, unresolved = resolve_surrogate_keys(
        sales, _versions([(1, "C1", "1900-01-01", None)]),
        _versions([(2, "P1", "1900-01-01", None)]))
    assert resolved["order_item_id"].tolist() == ["OI1"]
    assert unresolved["order_item_id"].tolist() == ["OI2"]


def test_customer_transformation_rules() -> None:
    raw = pd.DataFrame({
        "customer_id": ["C2", "C1"], "first_name": ["  ravi  ", "ASHA"], "last_name": ["", "rao"],
        "email": ["Ravi@X.IN", "asha@x.in"], "city": ["new   delhi", "HYDERABAD"],
        "state": ["delhi", "Telangana"], "country": ["", "India"],
        "created_at": ["2024-01-01 10:00:00", "2024-01-02T10:00:00"],
        "updated_at": ["", "2024-02-01 10:00:00"]})
    out = transform_customers(raw)
    assert out["customer_id"].tolist() == ["C1", "C2"]  # deterministic order
    c2 = out.iloc[1]
    assert (c2["first_name"], c2["city"], c2["state"], c2["country"]) == \
        ("Ravi", "New Delhi", "Delhi", "India")
    assert c2["last_name"] is None
    assert c2["email"] == "ravi@x.in"
    assert c2["updated_at"] == c2["created_at"]  # missing updated_at -> created_at
    assert out.iloc[0]["created_at"] == pd.Timestamp("2024-01-02 10:00:00")


def test_product_transformation_rules() -> None:
    raw = pd.DataFrame({"product_id": ["P1"], "product_name": [" boAt  Rockerz "],
                        "category": ["electronics"], "brand": [""], "unit_price": ["99.5"],
                        "created_at": ["2024-01-01 10:00:00"], "updated_at": [""]})
    out = transform_products(raw).iloc[0]
    assert out["product_name"] == "boAt Rockerz"  # mixed case preserved
    assert out["category"] == "Electronics"
    assert out["brand"] == "Unbranded"
    assert out["unit_price"] == D("99.50")


def test_transform_sales_end_to_end() -> None:
    orders = transform_orders(pd.DataFrame({
        "order_id": ["O1"], "customer_id": ["C1"], "order_date": ["2024-03-10 11:00:00"],
        "order_status": ["DELIVERED"], "payment_method": [""], "updated_at": [""]}))
    items = transform_order_items(pd.DataFrame({
        "order_item_id": ["OI2", "OI1"], "order_id": ["O1", "O1"], "product_id": ["P1", "P2"],
        "quantity": ["2", "1"], "unit_price": ["10.25", "5"], "discount_amount": ["", "0.50"],
        "created_at": ["2024-03-10 11:00:00"] * 2}), "order_items.csv")
    sales = transform_sales(items, orders)
    assert sales["order_item_id"].tolist() == ["OI1", "OI2"]
    assert sales["date_key"].tolist() == [20240310, 20240310]
    assert sales["payment_method"].tolist() == ["UNKNOWN", "UNKNOWN"]
    assert sales["gross_sales"].tolist() == [D("5.00"), D("20.50")]
    assert sales["net_revenue"].tolist() == [D("4.50"), D("20.50")]
    with pytest.raises(KeyResolutionError):
        transform_sales(items, orders.iloc[0:0])
