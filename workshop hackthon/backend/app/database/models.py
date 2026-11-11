"""Database model catalog.

The schema itself is defined in SQL (``other/sql/*.sql``, executed by
``app.database.connection.init_db``) because it relies on PostgreSQL features
an ORM would hide: partial unique indexes, gist exclusion constraints for SCD2
ranges, check constraints on measures and a hashing function. This module
describes those tables for the application (API catalog, whitelists) and maps
the operational tables with SQLAlchemy Core for typed reads.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (ARRAY, BigInteger, Column, DateTime, Integer, MetaData, Numeric, String,
                        Table, Text)
from sqlalchemy.dialects.postgresql import JSONB


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    kind: str          # fact | dimension | source | operational
    description: str


TABLE_CATALOG: tuple[TableInfo, ...] = (
    TableInfo("dw", "fact_sales", "fact", "Grain: one product line within one customer order"),
    TableInfo("dw", "dim_customer", "dimension", "Customers, SCD Type 2 on city/state/country"),
    TableInfo("dw", "dim_product", "dimension", "Products, SCD Type 2 on category/brand"),
    TableInfo("dw", "dim_date", "dimension", "Calendar, one row per day, key YYYYMMDD"),
    TableInfo("source", "customers", "source", "Typed source replica (ODS) of customers"),
    TableInfo("source", "products", "source", "Typed source replica (ODS) of products"),
    TableInfo("source", "orders", "source", "Typed source replica (ODS) of orders"),
    TableInfo("source", "order_items", "source", "Typed source replica (ODS) of order lines"),
    TableInfo("ops", "pipeline_runs", "operational", "One row per pipeline execution"),
    TableInfo("ops", "pipeline_metrics", "operational", "Name/value metrics per run"),
    TableInfo("ops", "rejected_records", "operational", "Quarantine: rejected source records"),
)
TABLES_BY_NAME: dict[str, TableInfo] = {t.name: t for t in TABLE_CATALOG}

metadata = MetaData()

pipeline_runs = Table(
    "pipeline_runs", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("pipeline_name", String(100), nullable=False),
    Column("start_time", DateTime(timezone=True), nullable=False),
    Column("end_time", DateTime(timezone=True)),
    Column("status", String(30), nullable=False),
    Column("source_file", Text),
    Column("input_count", BigInteger, nullable=False),
    Column("valid_count", BigInteger, nullable=False),
    Column("rejected_count", BigInteger, nullable=False),
    Column("inserted_count", BigInteger, nullable=False),
    Column("updated_count", BigInteger, nullable=False),
    Column("failed_count", BigInteger, nullable=False),
    Column("execution_duration_seconds", Numeric(12, 3)),
    Column("error_message", Text),
    schema="ops",
)

pipeline_metrics = Table(
    "pipeline_metrics", metadata,
    Column("metric_id", BigInteger, primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("metric_name", String(100), nullable=False),
    Column("metric_value", Numeric(20, 4), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    schema="ops",
)

rejected_records = Table(
    "rejected_records", metadata,
    Column("reject_id", BigInteger, primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("source_file", Text, nullable=False),
    Column("source_row_number", Integer, nullable=False),
    Column("source_record_id", Text),
    Column("entity_name", String(30), nullable=False),
    Column("rejection_reason", Text, nullable=False),
    Column("reason_codes", ARRAY(Text), nullable=False),
    Column("raw_record", JSONB, nullable=False),
    Column("rejected_at", DateTime(timezone=True), nullable=False),
    schema="ops",
)
