"""Database connectivity helpers (SQLAlchemy engine + psycopg 3 COPY support)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy import Connection, Engine, create_engine, make_url, text

from src.config.settings import SQL_DIR
from src.monitoring.logger import get_logger

log = get_logger("database")

SCHEMA_FILES: tuple[str, ...] = ("source_schema.sql", "warehouse_schema.sql", "indexes.sql")
MANAGED_SCHEMAS: tuple[str, ...] = ("raw", "source", "dw", "ops")


def get_engine(database_url: str, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine using the psycopg 3 driver."""
    url = make_url(database_url)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, echo=echo, pool_pre_ping=True, future=True)


def driver_connection(conn: Connection) -> psycopg.Connection:
    """Return the underlying psycopg connection of a SQLAlchemy connection (same transaction)."""
    dbapi_conn = conn.connection.driver_connection
    if not isinstance(dbapi_conn, psycopg.Connection):
        raise TypeError("COPY helpers require the psycopg (v3) driver")
    return dbapi_conn


def execute_script(conn: Connection, script: str) -> None:
    """Execute a multi-statement SQL script (no parameters) inside the current transaction."""
    with driver_connection(conn).cursor() as cur:
        cur.execute(script)


def init_db(engine: Engine, reset: bool = False) -> None:
    """Create (or optionally drop and recreate) every schema object.

    All DDL runs in one transaction, so a failure leaves the database unchanged.
    """
    with engine.begin() as conn:
        if reset:
            for schema in MANAGED_SCHEMAS:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            log.warning("Dropped schemas: %s", ", ".join(MANAGED_SCHEMAS))
        for file_name in SCHEMA_FILES:
            execute_script(conn, (SQL_DIR / file_name).read_text(encoding="utf-8"))
            log.info("Applied %s", file_name)


def copy_rows(conn: Connection, table: str, columns: Sequence[str],
              rows: Iterable[Sequence[Any]], schema: str | None = None) -> int:
    """Bulk load rows with PostgreSQL COPY (fastest bulk-insert path).

    Rows are streamed to the server; nothing is materialised twice in memory.
    Identifiers are quoted with ``psycopg.sql.Identifier`` so table/column names
    can never be used for SQL injection. ``None`` values become NULL.
    """
    target = sql.Identifier(schema, table) if schema else sql.Identifier(table)
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        target, sql.SQL(", ").join(sql.Identifier(c) for c in columns))
    count = 0
    with driver_connection(conn).cursor() as cur, cur.copy(stmt) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def database_name(database_url: str) -> str:
    """Return the database name component of a URL."""
    name = make_url(database_url).database
    if not name:
        raise ValueError("database URL has no database name")
    return name


def derive_database_url(database_url: str, suffix: str) -> str:
    """Return the same URL pointing at ``<dbname><suffix>`` (e.g. ``_test``, ``_bench``)."""
    url = make_url(database_url)
    return url.set(database=f"{url.database}{suffix}").render_as_string(hide_password=False)


def ensure_database_exists(database_url: str) -> bool:
    """Create the target database if it does not exist. Returns True when created."""
    url = make_url(database_url)
    target = url.database
    admin_engine = get_engine(url.set(database="postgres").render_as_string(hide_password=False))
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"),
                                  {"name": target}).scalar()
            if exists:
                return False
            with driver_connection(conn).cursor() as cur:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
            log.info("Created database %s", target)
            return True
    finally:
        admin_engine.dispose()
