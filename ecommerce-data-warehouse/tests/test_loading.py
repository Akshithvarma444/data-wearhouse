"""Loading tests: SCD2 dimension merges, fact upserts, constraints, transactions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from src.loading.load import (load_dim_customer, load_dim_date, load_dim_product,
                              upsert_fact_sales)
from src.scenarios import customer, product
from src.transformation.dimensions import (generate_date_dimension, transform_customers,
                                           transform_products)
from tests.conftest import scalar

D = Decimal
C1_HYD = customer("C1", "Hyderabad", "Telangana", "2024-01-01 10:00:00")
C1_CHE = customer("C1", "Chennai", "Tamil Nadu", "2024-06-01 00:00:00")
C2 = customer("C2", "Pune", "Maharashtra", "2024-01-01 10:00:00", first="Ravi")


def customers(*rows: dict[str, str]) -> pd.DataFrame:
    return transform_customers(pd.DataFrame(list(rows)))


def fact(item_id: str, customer_key: int, product_key: int, qty: int = 1,
         price: str = "10.00", discount: str = "0.00") -> dict[str, object]:
    gross = D(price) * qty
    return {"order_item_id": item_id, "order_id": "O1", "customer_key": customer_key,
            "product_key": product_key, "date_key": 20240310,
            "order_timestamp": pd.Timestamp("2024-03-10 11:00:00"), "order_status": "DELIVERED",
            "payment_method": "UPI", "quantity": qty, "unit_price": D(price),
            "gross_sales": gross, "discount_amount": D(discount),
            "net_revenue": gross - D(discount), "source_file": "order_items.csv"}


def seed_dimensions(engine: Engine) -> None:
    with engine.begin() as conn:
        load_dim_customer(conn, customers(C1_HYD, C2), "seed")
        load_dim_product(conn, transform_products(pd.DataFrame(
            [product("P1", "Electronics", "2024-01-01 10:00:00")])), "seed")
        load_dim_date(conn, generate_date_dimension(date(2024, 1, 1), date(2024, 12, 31)))


def test_dimension_load_creates_current_members(db: Engine) -> None:
    with db.begin() as conn:
        stats = load_dim_customer(conn, customers(C1_HYD, C2), "r1")
    assert (stats.new_members, stats.new_versions) == (2, 0)
    with db.connect() as conn:
        rows = conn.execute(text("""SELECT customer_id, effective_start_date, effective_end_date,
                                    is_current FROM dw.dim_customer ORDER BY customer_id""")).all()
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("C1", datetime(1900, 1, 1), datetime(9999, 12, 31), True),
        ("C2", datetime(1900, 1, 1), datetime(9999, 12, 31), True)]


def test_scd2_change_expires_old_and_creates_new_version(db: Engine) -> None:
    with db.begin() as conn:
        load_dim_customer(conn, customers(C1_HYD), "r1")
    with db.begin() as conn:
        stats = load_dim_customer(conn, customers(C1_CHE), "r2")
    assert (stats.new_members, stats.new_versions, stats.changed_business_keys) == (0, 1, ["C1"])
    with db.connect() as conn:
        rows = conn.execute(text("""SELECT customer_key, city, effective_start_date,
                                           effective_end_date, is_current
                                    FROM dw.dim_customer ORDER BY effective_start_date""")).all()
    assert len(rows) == 2
    old, new = rows
    assert (old.city, old.is_current, old.effective_end_date) == \
        ("Hyderabad", False, datetime(2024, 6, 1))
    assert (new.city, new.is_current, new.effective_start_date) == \
        ("Chennai", True, datetime(2024, 6, 1))
    assert new.customer_key != old.customer_key


def test_dimension_rerun_creates_no_duplicates(db: Engine) -> None:
    for run in ("r1", "r2", "r3"):
        with db.begin() as conn:
            stats = load_dim_customer(conn, customers(C1_HYD, C2), run)
    assert (stats.new_members, stats.new_versions, stats.type1_updates) == (0, 0, 0)
    assert scalar(db, "SELECT count(*) FROM dw.dim_customer") == 2


def test_type1_attribute_overwrites_without_new_version(db: Engine) -> None:
    with db.begin() as conn:
        load_dim_customer(conn, customers(C1_HYD), "r1")
    renamed = {**C1_HYD, "last_name": "Reddy-Rao", "updated_at": "2024-02-01 10:00:00"}
    with db.begin() as conn:
        stats = load_dim_customer(conn, customers(renamed), "r2")
    assert (stats.type1_updates, stats.new_versions) == (1, 0)
    assert scalar(db, "SELECT count(*) FROM dw.dim_customer") == 1
    assert scalar(db, "SELECT last_name FROM dw.dim_customer") == "Reddy-Rao"


def test_out_of_order_dimension_update_is_ignored(db: Engine) -> None:
    with db.begin() as conn:
        load_dim_customer(conn, customers(C1_CHE), "r1")
    with db.begin() as conn:
        stats = load_dim_customer(conn, customers(C1_HYD), "r2")  # older updated_at
    assert stats.stale_ignored == 1 and stats.new_versions == 0
    assert scalar(db, "SELECT city FROM dw.dim_customer WHERE is_current") == "Chennai"


def test_product_scd2_on_category_change(db: Engine) -> None:
    with db.begin() as conn:
        load_dim_product(conn, transform_products(pd.DataFrame(
            [product("P1", "Electronics", "2024-01-01 10:00:00")])), "r1")
        stats = load_dim_product(conn, transform_products(pd.DataFrame(
            [product("P1", "Mobiles", "2024-05-01 00:00:00", price="899.00")])), "r2")
    assert stats.new_versions == 1
    assert scalar(db, "SELECT count(*) FROM dw.dim_product") == 2
    # unit_price is Type 1: the latest list price is on every version
    assert scalar(db, "SELECT count(DISTINCT unit_price) FROM dw.dim_product") == 1


def test_date_dimension_load_is_idempotent(db: Engine) -> None:
    dates = generate_date_dimension(date(2024, 1, 1), date(2024, 12, 31))
    with db.begin() as conn:
        assert load_dim_date(conn, dates) == 366
        assert load_dim_date(conn, dates) == 0


def test_successful_fact_load(db: Engine) -> None:
    seed_dimensions(db)
    facts = pd.DataFrame([fact("OI1", 1, 1, qty=2, price="99.99", discount="9.99"),
                          fact("OI2", 2, 1)])
    with db.begin() as conn:
        assert upsert_fact_sales(conn, facts, "r1", "ecommerce_oltp") == (2, 0, 0)
    # (2 x 99.99 - 9.99) + (1 x 10.00) = 189.99 + 10.00
    assert scalar(db, "SELECT sum(net_revenue) FROM dw.fact_sales") == D("199.99")
    assert scalar(db, "SELECT count(DISTINCT record_hash) FROM dw.fact_sales") == 2


def test_foreign_keys_are_enforced(db: Engine) -> None:
    seed_dimensions(db)
    with pytest.raises(IntegrityError, match="fk_fact_sales_customer"):
        with db.begin() as conn:
            upsert_fact_sales(conn, pd.DataFrame([fact("OI1", 999, 1)]), "r1", "ecommerce_oltp")
    with pytest.raises(IntegrityError, match="fk_fact_sales_date"):
        with db.begin() as conn:
            bad_date = {**fact("OI1", 1, 1), "date_key": 19990101}
            upsert_fact_sales(conn, pd.DataFrame([bad_date]), "r1", "ecommerce_oltp")
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 0


def test_duplicate_fact_prevention(db: Engine) -> None:
    seed_dimensions(db)
    facts = pd.DataFrame([fact("OI1", 1, 1)])
    with db.begin() as conn:
        assert upsert_fact_sales(conn, facts, "r1", "ecommerce_oltp") == (1, 0, 0)
        assert upsert_fact_sales(conn, facts, "r2", "ecommerce_oltp") == (0, 0, 1)
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 1
    assert scalar(db, "SELECT run_id FROM dw.fact_sales") == "r1"  # untouched by no-op
    # the unique event key also blocks plain INSERTs
    with pytest.raises(IntegrityError, match="uq_fact_sales_event"):
        with db.begin() as conn:
            conn.execute(text("""
                INSERT INTO dw.fact_sales (source_system, order_item_id, order_id, customer_key,
                    product_key, date_key, order_timestamp, order_status, payment_method,
                    quantity, unit_price, gross_sales, discount_amount, net_revenue, record_hash,
                    source_file, run_id)
                SELECT source_system, order_item_id, order_id, customer_key, product_key,
                       date_key, order_timestamp, order_status, payment_method, quantity,
                       unit_price, gross_sales, discount_amount, net_revenue, record_hash,
                       source_file, 'r3'
                FROM dw.fact_sales"""))


def test_corrected_fact_updates_in_place(db: Engine) -> None:
    seed_dimensions(db)
    with db.begin() as conn:
        upsert_fact_sales(conn, pd.DataFrame([fact("OI1", 1, 1, qty=1)]), "r1", "ecommerce_oltp")
        assert upsert_fact_sales(conn, pd.DataFrame([fact("OI1", 1, 1, qty=4)]), "r2",
                                 "ecommerce_oltp") == (0, 1, 0)
    assert scalar(db, "SELECT gross_sales FROM dw.fact_sales") == D("40.00")
    assert scalar(db, "SELECT run_id FROM dw.fact_sales") == "r2"


def test_measure_check_constraints(db: Engine) -> None:
    seed_dimensions(db)
    wrong = {**fact("OI1", 1, 1), "net_revenue": D("11.00")}
    with pytest.raises(IntegrityError, match="ck_fact_sales_net"):
        with db.begin() as conn:
            upsert_fact_sales(conn, pd.DataFrame([wrong]), "r1", "ecommerce_oltp")


def test_transaction_rollback_leaves_no_partial_data(db: Engine) -> None:
    with pytest.raises(RuntimeError, match="simulated"):
        with db.begin() as conn:
            load_dim_customer(conn, customers(C1_HYD, C2), "r1")
            assert conn.execute(text("SELECT count(*) FROM dw.dim_customer")).scalar() == 2
            raise RuntimeError("simulated failure after dimension load")
    assert scalar(db, "SELECT count(*) FROM dw.dim_customer") == 0


def test_only_one_current_version_allowed(db: Engine) -> None:
    with db.begin() as conn:
        load_dim_customer(conn, customers(C1_HYD), "r1")
    with pytest.raises(IntegrityError, match="ux_dim_customer_current|ex_dim_customer_no_overlap"):
        with db.begin() as conn:
            conn.execute(text("""
                INSERT INTO dw.dim_customer (customer_id, first_name, email, city, state, country,
                    effective_start_date, is_current, source_updated_at, run_id)
                VALUES ('C1', 'X', 'x@y.in', 'Goa', 'Goa', 'India', '2030-01-01', TRUE,
                        '2030-01-01', 'bad')"""))


def test_overlapping_versions_rejected(db: Engine) -> None:
    with db.begin() as conn:
        load_dim_customer(conn, customers(C1_HYD), "r1")
    with pytest.raises(IntegrityError, match="ex_dim_customer_no_overlap"):
        with db.begin() as conn:
            conn.execute(text("""
                INSERT INTO dw.dim_customer (customer_id, first_name, email, city, state, country,
                    effective_start_date, effective_end_date, is_current, source_updated_at, run_id)
                VALUES ('C1', 'X', 'x@y.in', 'Goa', 'Goa', 'India', '2020-01-01', '2021-01-01',
                        FALSE, '2020-01-01', 'bad')"""))
