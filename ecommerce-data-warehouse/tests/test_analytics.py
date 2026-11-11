"""Analytical SQL tests: query results vs. independently computed expected values."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine

from src.analytics import load_queries, run_query
from src.pipeline import run_pipeline
from tests.conftest import SAMPLE_DIR, scalar
from tests.fixtures.build_expected_results import OUTPUT, compute_expected

EXPECTED = json.loads(OUTPUT.read_text(encoding="utf-8"))


@pytest.fixture
def loaded(db: Engine, reports_dir: Path) -> Engine:
    assert run_pipeline(SAMPLE_DIR, engine=db, reports_dir=reports_dir).status == "SUCCESS"
    return db


def as_map(frame, key: str, value: str) -> dict[str, str]:
    return {str(k): str(v) for k, v in zip(frame[key], frame[value])}


def test_fixture_is_current() -> None:
    """The committed fixture matches a fresh computation from the sample CSVs."""
    assert compute_expected(SAMPLE_DIR) == EXPECTED


def test_all_queries_execute(loaded: Engine) -> None:
    queries = load_queries()
    assert len(queries) >= 12
    for name in queries:
        run_query(loaded, name, queries)


def test_total_revenue(loaded: Engine) -> None:
    row = run_query(loaded, "total_revenue").iloc[0]
    expected = EXPECTED["total_revenue"]
    for column in ("orders", "order_lines", "units_sold"):
        assert int(row[column]) == expected[column]
    for column in ("gross_sales", "discounts", "net_revenue"):
        assert str(row[column]) == expected[column]


def test_monthly_and_yearly_revenue_add_up(loaded: Engine) -> None:
    yearly = run_query(loaded, "yearly_revenue")
    assert as_map(yearly, "year", "net_revenue") == EXPECTED["yearly_revenue"]
    monthly = run_query(loaded, "monthly_revenue")
    assert sum(monthly["net_revenue"], Decimal("0")) == \
        Decimal(EXPECTED["total_revenue"]["net_revenue"])


def test_category_city_and_payment_revenue(loaded: Engine) -> None:
    assert as_map(run_query(loaded, "category_revenue"), "category", "net_revenue") == \
        EXPECTED["category_revenue"]
    assert as_map(run_query(loaded, "city_revenue"), "city", "net_revenue") == \
        EXPECTED["city_revenue"]
    payments = run_query(loaded, "revenue_by_payment_method")
    assert as_map(payments, "payment_method", "net_revenue") == \
        EXPECTED["revenue_by_payment_method"]
    assert {k: int(v) for k, v in as_map(payments, "payment_method", "orders").items()} == \
        EXPECTED["orders_by_payment_method"]


def test_top_products_and_status(loaded: Engine) -> None:
    assert run_query(loaded, "top_10_products")["product_id"].tolist() == \
        EXPECTED["top_10_products"]
    status = run_query(loaded, "order_status_analysis")
    assert {k: int(v) for k, v in as_map(status, "order_status", "orders").items()} == \
        EXPECTED["order_status_orders"]


def test_average_order_value_is_order_level(loaded: Engine) -> None:
    row = run_query(loaded, "average_order_value").iloc[0]
    assert str(row["average_order_value"]) == EXPECTED["average_order_value"]
    # a line-level average would be a different (wrong) number
    line_avg = scalar(loaded, "SELECT round(avg(net_revenue), 2) FROM dw.fact_sales "
                              "WHERE order_status NOT IN ('CANCELLED','RETURNED')")
    assert str(line_avg) != EXPECTED["average_order_value"]


def test_dimension_joins_do_not_double_count(loaded: Engine) -> None:
    """Joining every dimension must not change the fact total."""
    joined = scalar(loaded, """
        SELECT sum(f.net_revenue) FROM dw.fact_sales f
        JOIN dw.dim_customer c ON c.customer_key = f.customer_key
        JOIN dw.dim_product p ON p.product_key = f.product_key
        JOIN dw.dim_date d ON d.date_key = f.date_key""")
    assert str(joined) == EXPECTED["all_lines_net_revenue"]
    total_qty = run_query(loaded, "total_quantity_sold")
    assert int(total_qty.iloc[0]["units_sold"]) == EXPECTED["total_revenue"]["units_sold"]
