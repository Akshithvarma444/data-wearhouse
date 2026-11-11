"""Shared pytest fixtures.

Database tests run against an ISOLATED database (TEST_DATABASE_URL, or
<DB_NAME>_test on the configured server). The suite refuses to run against any
database whose name does not end with ``_test``, so the developer's warehouse
is never modified. Tables are truncated before every database test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from src.config.settings import PROJECT_ROOT, ConfigurationError, Settings, get_settings
from src.loading.database import (database_name, derive_database_url, ensure_database_exists,
                                  get_engine, init_db)
from src.scenarios import customer, item, order, product, write_batch

SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"

ALL_TABLES = ("raw.customers, raw.products, raw.orders, raw.order_items, "
              "source.order_items, source.orders, source.products, source.customers, "
              "dw.fact_sales, dw.dim_customer, dw.dim_product, dw.dim_date, "
              "ops.pipeline_metrics, ops.rejected_records, ops.source_files, ops.pipeline_runs")


@pytest.fixture(scope="session")
def settings() -> Settings:
    try:
        return get_settings()
    except ConfigurationError as exc:
        pytest.skip(f"configuration missing ({exc}); copy .env.example to .env")


@pytest.fixture(scope="session")
def test_database_url(settings: Settings) -> str:
    url = settings.test_database_url or derive_database_url(settings.database_url, "_test")
    name = database_name(url)
    if not name.endswith("_test"):
        pytest.exit(f"Refusing to run destructive tests against database '{name}' "
                    f"(name must end with '_test')", returncode=3)
    try:
        ensure_database_exists(url)
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable for tests: {exc.orig}")
    return url


@pytest.fixture(scope="session")
def engine(test_database_url: str) -> Iterator[Engine]:
    eng = get_engine(test_database_url)
    init_db(eng, reset=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Engine:
    """Empty warehouse (all tables truncated, identities reset)."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))
    return engine


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    path = tmp_path / "reports"
    path.mkdir()
    return path


def scalar(engine: Engine, sql: str, **params: object) -> object:
    """Run a scalar query (helper for assertions)."""
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


@pytest.fixture
def small_batch(tmp_path: Path) -> Path:
    """Two customers, two products, two orders, three valid lines."""
    return write_batch(
        tmp_path / "small",
        customers=[customer("C1", "Hyderabad", "Telangana", "2024-01-01 10:00:00"),
                   customer("C2", "Pune", "Maharashtra", "2024-01-01 10:00:00", first="Ravi")],
        products=[product("P1", "Electronics", "2024-01-01 10:00:00"),
                  product("P2", "Books", "2024-01-01 10:00:00", name="Novel", brand="Penguin",
                          price="250.50")],
        orders=[order("O1", "C1", "2024-03-10 11:00:00"),
                order("O2", "C2", "2024-03-11 12:00:00", status="SHIPPED", payment="CREDIT_CARD")],
        order_items=[item("OI1", "O1", "P1", 2, "1000.00", "100.00", "2024-03-10 11:00:00"),
                     item("OI2", "O1", "P2", 3, "250.50", "0.00", "2024-03-10 11:00:00"),
                     item("OI3", "O2", "P2", 1, "250.50", "25.05", "2024-03-11 12:00:00")])
