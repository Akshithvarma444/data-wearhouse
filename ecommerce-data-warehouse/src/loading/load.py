"""Database loading: raw landing, quarantine, source (ODS) upserts, SCD2 dimensions, facts.

Transaction boundaries (orchestrated by ``src.pipeline``)
--------------------------------------------------------
1. ``land_raw_and_rejects``  - own transaction, committed before the warehouse
   load, so raw records and quarantined rows survive a later failure.
2. Everything from ``upsert_source_*`` to ``realign_fact_keys`` runs inside ONE
   transaction (``engine.begin()``) guarded by an advisory lock. Any exception
   rolls back the source upserts, dimensions and facts together: an
   inconsistent fact batch is never published.

All bulk writes use PostgreSQL COPY into a temporary staging table followed by a
single set-based ``INSERT ... SELECT ... ON CONFLICT`` statement.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb
from sqlalchemy import Connection, Engine, text

from src.ingestion.extract import ROW_NUMBER_COLUMN, ExtractedEntity
from src.loading.database import copy_rows, execute_script
from src.monitoring.logger import get_logger
from src.validation.schemas import ENTITY_LOAD_ORDER, ENTITY_SPECS
from src.validation.validate import EntityValidationResult

log = get_logger("loading")

ADVISORY_LOCK_KEY = 815_204_771  # serialises warehouse loads across processes


class DataIntegrityError(RuntimeError):
    """A consistency rule failed inside the warehouse transaction (forces rollback)."""


@dataclass
class DimensionLoadStats:
    """Outcome of one SCD2 dimension merge."""

    new_members: int = 0
    new_versions: int = 0
    type1_updates: int = 0
    stale_ignored: int = 0
    changed_business_keys: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- helpers
def frame_rows(frame: pd.DataFrame, columns: Sequence[str]) -> Iterator[tuple[Any, ...]]:
    """Yield plain-Python row tuples (NaN/NaT -> None) suitable for COPY."""
    lists = []
    for column in columns:
        series = frame[column]
        values = series.to_numpy(dtype=object)  # vectorised; avoids per-element iteration
        missing = series.isna().to_numpy()
        if missing.any():
            values = values.copy()
            values[missing] = None
        lists.append(values.tolist())
    return zip(*lists)


def _create_temp(conn: Connection, name: str, ddl_columns: str) -> None:
    execute_script(conn, f"DROP TABLE IF EXISTS {name}; "
                         f"CREATE TEMP TABLE {name} ({ddl_columns}) ON COMMIT DROP;")


def acquire_load_lock(conn: Connection) -> None:
    """Transaction-scoped advisory lock: only one warehouse load at a time."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})


# ----------------------------------------------------------------------------- references
class DbReferenceLookup:
    """``ReferenceLookup`` implementation backed by the source (ODS) tables."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _ids(self, sql: str, ids: set[str]) -> set[str]:
        if not ids:
            return set()
        with self._engine.connect() as conn:
            return set(conn.execute(text(sql), {"ids": sorted(ids)}).scalars())

    def existing_customer_ids(self, ids: set[str]) -> set[str]:
        return self._ids("SELECT customer_id FROM source.customers "
                         "WHERE customer_id = ANY(CAST(:ids AS text[]))", ids)

    def existing_product_ids(self, ids: set[str]) -> set[str]:
        return self._ids("SELECT product_id FROM source.products "
                         "WHERE product_id = ANY(CAST(:ids AS text[]))", ids)

    def existing_order_ids(self, ids: set[str]) -> set[str]:
        return self._ids("SELECT order_id FROM source.orders "
                         "WHERE order_id = ANY(CAST(:ids AS text[]))", ids)

    def order_dates(self, ids: set[str]) -> dict[str, Any]:
        """order_id -> order_date for orders already in the source layer."""
        if not ids:
            return {}
        with self._engine.connect() as conn:
            rows = conn.execute(text("SELECT order_id, order_date FROM source.orders "
                                     "WHERE order_id = ANY(CAST(:ids AS text[]))"),
                                {"ids": sorted(ids)})
            return {order_id: order_date for order_id, order_date in rows}

    def email_owners(self, emails: set[str]) -> dict[str, str]:
        if not emails:
            return {}
        with self._engine.connect() as conn:
            rows = conn.execute(text("SELECT email, customer_id FROM source.customers "
                                     "WHERE email = ANY(CAST(:emails AS text[]))"),
                                {"emails": sorted(emails)})
            return {email: customer_id for email, customer_id in rows}


# ----------------------------------------------------------------------------- landing
def land_raw_and_rejects(engine: Engine, run_id: str, extracted: dict[str, ExtractedEntity],
                         results: dict[str, EntityValidationResult]) -> dict[str, int]:
    """Persist every input record (raw.*), the file registry and the quarantine.

    Runs in its own committed transaction. Returns counts per target table.
    """
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for entity in ENTITY_LOAD_ORDER:
            ex, res, spec = extracted[entity], results[entity], ENTITY_SPECS[entity]
            conn.execute(text("""
                INSERT INTO ops.source_files (run_id, entity_name, file_name, file_path,
                    file_sha256, file_size_bytes, record_count, ingested_at)
                VALUES (:run_id, :entity, :file_name, :file_path, :sha, :size, :count, :ts)
            """), {"run_id": run_id, "entity": entity, "file_name": ex.source_file,
                   "file_path": str(ex.file_path), "sha": ex.file_sha256,
                   "size": ex.file_size_bytes, "count": ex.record_count, "ts": ex.ingested_at})

            data = ex.data
            row_numbers = data[ROW_NUMBER_COLUMN].to_numpy()
            rejected_rows = np.array([r.source_row_number for r in res.rejected], dtype=np.int64)
            skipped_rows = (res.skipped[ROW_NUMBER_COLUMN].to_numpy(dtype=np.int64)
                            if res.skipped is not None and len(res.skipped) else
                            np.array([], dtype=np.int64))
            status = np.where(np.isin(row_numbers, rejected_rows), "REJECTED",
                              np.where(np.isin(row_numbers, skipped_rows), "SKIPPED", "VALID"))
            landing = data[list(spec.columns)].copy()
            landing["run_id"] = run_id
            landing["source_file"] = ex.source_file
            landing["source_row_number"] = row_numbers
            landing["validation_status"] = status
            landing["ingested_at"] = ex.ingested_at
            if ex.unexpected_columns:
                landing["extra_attributes"] = [Jsonb(r) for r in
                                               data[ex.unexpected_columns].to_dict("records")]
            else:
                landing["extra_attributes"] = None
            columns = ["run_id", "source_file", "source_row_number", *spec.columns,
                       "extra_attributes", "validation_status", "ingested_at"]
            landed = copy_rows(conn, entity, columns, frame_rows(landing, columns), schema="raw")
            malformed = [(run_id, ex.source_file, bad.source_row_number,
                          *([None] * len(spec.columns)), Jsonb({"raw_fields": bad.fields}),
                          "REJECTED", ex.ingested_at) for bad in ex.malformed_rows]
            landed += copy_rows(conn, entity, columns, malformed, schema="raw")
            counts[f"raw_{entity}"] = landed

        reject_columns = ["run_id", "source_file", "source_row_number", "source_record_id",
                          "entity_name", "rejection_reason", "reason_codes", "raw_record"]
        reject_rows = ((run_id, r.source_file, r.source_row_number, r.source_record_id,
                        r.entity_name, r.rejection_reason, r.reason_codes, Jsonb(r.raw_record))
                       for res in results.values() for r in res.rejected)
        counts["rejected_records"] = copy_rows(conn, "rejected_records", reject_columns,
                                               reject_rows, schema="ops")
    log.info("Landed raw records %s and %d quarantined record(s)",
             {k: v for k, v in counts.items() if k.startswith("raw_")}, counts["rejected_records"])
    return counts


# ----------------------------------------------------------------------------- source (ODS)
def _upsert_counts(conn: Connection, sql: str, params: dict[str, Any]) -> tuple[int, int]:
    row = conn.execute(text(sql), params).one()
    return int(row[0] or 0), int(row[1] or 0)


def upsert_source_customers(conn: Connection, customers: pd.DataFrame, run_id: str,
                            source_file: str) -> tuple[int, int]:
    """Upsert into source.customers. Newer ``updated_at`` wins; stale rows are ignored."""
    cols = ["customer_id", "first_name", "last_name", "email", "city", "state", "country",
            "created_at", "updated_at"]
    _create_temp(conn, "tmp_stage_customers",
                 "customer_id varchar(20), first_name varchar(100), last_name varchar(100), "
                 "email varchar(255), city varchar(100), state varchar(100), country varchar(100), "
                 "created_at timestamp, updated_at timestamp")
    copy_rows(conn, "tmp_stage_customers", cols, frame_rows(customers, cols))
    return _upsert_counts(conn, """
        WITH up AS (
            INSERT INTO source.customers AS c (customer_id, first_name, last_name, email, city,
                state, country, created_at, updated_at, last_run_id, last_source_file)
            SELECT customer_id, first_name, last_name, email, city, state, country, created_at,
                   updated_at, :run_id, :source_file
            FROM tmp_stage_customers
            ON CONFLICT (customer_id) DO UPDATE SET
                first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name,
                email = EXCLUDED.email, city = EXCLUDED.city, state = EXCLUDED.state,
                country = EXCLUDED.country, created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at, last_run_id = EXCLUDED.last_run_id,
                last_source_file = EXCLUDED.last_source_file, loaded_at = now()
            WHERE EXCLUDED.updated_at >= c.updated_at
              AND (c.first_name, c.last_name, c.email, c.city, c.state, c.country,
                   c.created_at, c.updated_at)
                  IS DISTINCT FROM
                  (EXCLUDED.first_name, EXCLUDED.last_name, EXCLUDED.email, EXCLUDED.city,
                   EXCLUDED.state, EXCLUDED.country, EXCLUDED.created_at, EXCLUDED.updated_at)
            RETURNING (xmax = 0) AS inserted)
        SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
    """, {"run_id": run_id, "source_file": source_file})


def upsert_source_products(conn: Connection, products: pd.DataFrame, run_id: str,
                           source_file: str) -> tuple[int, int]:
    """Upsert into source.products. Newer ``updated_at`` wins; stale rows are ignored."""
    cols = ["product_id", "product_name", "category", "brand", "unit_price", "created_at",
            "updated_at"]
    _create_temp(conn, "tmp_stage_products",
                 "product_id varchar(20), product_name varchar(200), category varchar(100), "
                 "brand varchar(100), unit_price numeric(12,2), created_at timestamp, "
                 "updated_at timestamp")
    copy_rows(conn, "tmp_stage_products", cols, frame_rows(products, cols))
    return _upsert_counts(conn, """
        WITH up AS (
            INSERT INTO source.products AS p (product_id, product_name, category, brand,
                unit_price, created_at, updated_at, last_run_id, last_source_file)
            SELECT product_id, product_name, category, brand, unit_price, created_at,
                   updated_at, :run_id, :source_file
            FROM tmp_stage_products
            ON CONFLICT (product_id) DO UPDATE SET
                product_name = EXCLUDED.product_name, category = EXCLUDED.category,
                brand = EXCLUDED.brand, unit_price = EXCLUDED.unit_price,
                created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at,
                last_run_id = EXCLUDED.last_run_id, last_source_file = EXCLUDED.last_source_file,
                loaded_at = now()
            WHERE EXCLUDED.updated_at >= p.updated_at
              AND (p.product_name, p.category, p.brand, p.unit_price, p.created_at, p.updated_at)
                  IS DISTINCT FROM
                  (EXCLUDED.product_name, EXCLUDED.category, EXCLUDED.brand,
                   EXCLUDED.unit_price, EXCLUDED.created_at, EXCLUDED.updated_at)
            RETURNING (xmax = 0) AS inserted)
        SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
    """, {"run_id": run_id, "source_file": source_file})


def upsert_source_orders(conn: Connection, orders: pd.DataFrame, run_id: str,
                         source_file: str) -> tuple[int, int, list[str]]:
    """Upsert into source.orders. Returns (inserted, updated, ids of updated orders)."""
    cols = ["order_id", "customer_id", "order_timestamp", "order_status", "payment_method",
            "updated_at"]
    _create_temp(conn, "tmp_stage_orders",
                 "order_id varchar(30), customer_id varchar(20), order_timestamp timestamp, "
                 "order_status varchar(20), payment_method varchar(30), updated_at timestamp")
    _create_temp(conn, "tmp_changed_orders", "order_id varchar(30)")
    copy_rows(conn, "tmp_stage_orders", cols, frame_rows(orders, cols))
    inserted, updated = _upsert_counts(conn, """
        WITH up AS (
            INSERT INTO source.orders AS o (order_id, customer_id, order_date, order_status,
                payment_method, updated_at, last_run_id, last_source_file)
            SELECT order_id, customer_id, order_timestamp, order_status, payment_method,
                   updated_at, :run_id, :source_file
            FROM tmp_stage_orders
            ON CONFLICT (order_id) DO UPDATE SET
                customer_id = EXCLUDED.customer_id, order_date = EXCLUDED.order_date,
                order_status = EXCLUDED.order_status, payment_method = EXCLUDED.payment_method,
                updated_at = EXCLUDED.updated_at, last_run_id = EXCLUDED.last_run_id,
                last_source_file = EXCLUDED.last_source_file, loaded_at = now()
            WHERE EXCLUDED.updated_at >= o.updated_at
              AND (o.customer_id, o.order_date, o.order_status, o.payment_method, o.updated_at)
                  IS DISTINCT FROM
                  (EXCLUDED.customer_id, EXCLUDED.order_date, EXCLUDED.order_status,
                   EXCLUDED.payment_method, EXCLUDED.updated_at)
            RETURNING o.order_id, (xmax = 0) AS inserted),
        changed AS (
            INSERT INTO tmp_changed_orders (order_id)
            SELECT order_id FROM up WHERE NOT inserted)
        SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
    """, {"run_id": run_id, "source_file": source_file})
    changed = list(conn.execute(text("SELECT order_id FROM tmp_changed_orders")).scalars())
    return inserted, updated, changed


def upsert_source_order_items(conn: Connection, items: pd.DataFrame, run_id: str) -> tuple[int, int]:
    """Upsert into source.order_items. A re-delivered line with new content is a correction."""
    cols = ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
            "discount_amount", "created_at", "source_file"]
    _create_temp(conn, "tmp_stage_order_items",
                 "order_item_id varchar(30), order_id varchar(30), product_id varchar(20), "
                 "quantity integer, unit_price numeric(12,2), discount_amount numeric(12,2), "
                 "created_at timestamp, source_file text")
    copy_rows(conn, "tmp_stage_order_items", cols, frame_rows(items, cols))
    return _upsert_counts(conn, """
        WITH up AS (
            INSERT INTO source.order_items AS i (order_item_id, order_id, product_id, quantity,
                unit_price, discount_amount, created_at, last_run_id, last_source_file)
            SELECT order_item_id, order_id, product_id, quantity, unit_price, discount_amount,
                   created_at, :run_id, source_file
            FROM tmp_stage_order_items
            ON CONFLICT (order_item_id) DO UPDATE SET
                order_id = EXCLUDED.order_id, product_id = EXCLUDED.product_id,
                quantity = EXCLUDED.quantity, unit_price = EXCLUDED.unit_price,
                discount_amount = EXCLUDED.discount_amount, created_at = EXCLUDED.created_at,
                last_run_id = EXCLUDED.last_run_id, last_source_file = EXCLUDED.last_source_file,
                loaded_at = now()
            WHERE (i.order_id, i.product_id, i.quantity, i.unit_price, i.discount_amount,
                   i.created_at)
                  IS DISTINCT FROM
                  (EXCLUDED.order_id, EXCLUDED.product_id, EXCLUDED.quantity,
                   EXCLUDED.unit_price, EXCLUDED.discount_amount, EXCLUDED.created_at)
            RETURNING (xmax = 0) AS inserted)
        SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
    """, {"run_id": run_id})


def fetch_order_headers(conn: Connection, order_ids: set[str]) -> pd.DataFrame:
    """Order headers from source.orders (used for lines whose order is not in this batch)."""
    columns = ["order_id", "customer_id", "order_timestamp", "order_status", "payment_method",
               "updated_at"]
    if not order_ids:
        return pd.DataFrame(columns=columns)
    rows = conn.execute(text("""
        SELECT order_id, customer_id, order_date, order_status, payment_method, updated_at
        FROM source.orders WHERE order_id = ANY(CAST(:ids AS text[]))
    """), {"ids": sorted(order_ids)}).all()
    return pd.DataFrame(rows, columns=columns)


def fetch_lines_of_changed_orders(conn: Connection, order_ids: list[str]) -> pd.DataFrame:
    """Existing order lines of orders whose header changed in this run but which are not
    re-delivered in this batch. Their facts are rebuilt so status/date/customer changes
    propagate to every line of the order."""
    columns = ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
               "discount_amount", "created_at", "source_file"]
    if not order_ids:
        return pd.DataFrame(columns=columns)
    rows = conn.execute(text("""
        SELECT oi.order_item_id, oi.order_id, oi.product_id, oi.quantity, oi.unit_price,
               oi.discount_amount, oi.created_at, oi.last_source_file
        FROM source.order_items oi
        WHERE oi.order_id = ANY(CAST(:ids AS text[]))
          AND NOT EXISTS (SELECT 1 FROM tmp_stage_order_items s
                          WHERE s.order_item_id = oi.order_item_id)
    """), {"ids": order_ids}).all()
    frame = pd.DataFrame(rows, columns=columns)
    frame["quantity"] = frame["quantity"].astype("int64")
    return frame


# ----------------------------------------------------------------------------- dimensions
_SCD_CONFIG: dict[str, dict[str, Any]] = {
    "customer": {
        "table": "dw.dim_customer", "key": "customer_key", "bk": "customer_id",
        "type1": ["first_name", "last_name", "email"],
        "type2": ["city", "state", "country"],
        "stage_ddl": ("customer_id varchar(20), first_name varchar(100), last_name varchar(100), "
                      "email varchar(255), city varchar(100), state varchar(100), "
                      "country varchar(100), source_updated_at timestamp"),
    },
    "product": {
        "table": "dw.dim_product", "key": "product_key", "bk": "product_id",
        "type1": ["product_name", "unit_price"],
        "type2": ["category", "brand"],
        "stage_ddl": ("product_id varchar(20), product_name varchar(200), category varchar(100), "
                      "brand varchar(100), unit_price numeric(12,2), source_updated_at timestamp"),
    },
}


def _load_scd2_dimension(conn: Connection, kind: str, frame: pd.DataFrame,
                         run_id: str) -> DimensionLoadStats:
    """Generic SCD Type 2 merge (set-based SQL; identifiers come from a fixed whitelist).

    * Type 1 attributes overwrite every version of the key (no history kept).
    * Type 2 attributes: if changed and the source change is newer than the current
      version's start, the current version is closed at ``source_updated_at`` and a
      new current version starts at the same instant (half-open ranges, no gaps).
    * Out-of-order (stale) source rows, i.e. ``source_updated_at`` older than the
      current version's ``source_updated_at``, are ignored and counted.
    * The first version of a new key starts at 1900-01-01 so facts dated before the
      customer's first recorded change still resolve.
    """
    cfg = _SCD_CONFIG[kind]
    table, bk = cfg["table"], cfg["bk"]
    t1, t2 = cfg["type1"], cfg["type2"]
    attrs = t1 + t2
    stage = f"tmp_dim_{kind}_in"
    stats = DimensionLoadStats()
    if frame.empty:
        return stats

    staged = frame.rename(columns={"updated_at": "source_updated_at"})
    _create_temp(conn, stage, cfg["stage_ddl"])
    stage_cols = [bk, *attrs, "source_updated_at"]
    copy_rows(conn, stage, stage_cols, frame_rows(staged, stage_cols))

    def tup(alias: str, cols: list[str]) -> str:
        return "(" + ", ".join(f"{alias}.{c}" for c in cols) + ")"

    stats.stale_ignored = int(conn.execute(text(f"""
        SELECT count(*) FROM {stage} t JOIN {table} d ON d.{bk} = t.{bk} AND d.is_current
        WHERE t.source_updated_at < d.source_updated_at
          AND {tup('d', attrs)} IS DISTINCT FROM {tup('t', attrs)}
    """)).scalar_one())

    stats.type1_updates = int(conn.execute(text(f"""
        WITH t1 AS (
            UPDATE {table} d SET
                {", ".join(f"{c} = t.{c}" for c in t1)},
                source_updated_at = CASE WHEN d.is_current
                                         THEN GREATEST(d.source_updated_at, t.source_updated_at)
                                         ELSE d.source_updated_at END,
                run_id = :run_id, updated_at = now()
            FROM {stage} t
            JOIN {table} cur ON cur.{bk} = t.{bk} AND cur.is_current
            WHERE d.{bk} = t.{bk}
              AND t.source_updated_at >= cur.source_updated_at
              AND {tup('d', t1)} IS DISTINCT FROM {tup('t', t1)}
            RETURNING d.{bk})
        SELECT count(DISTINCT {bk}) FROM t1
    """), {"run_id": run_id}).scalar_one())

    expired = list(conn.execute(text(f"""
        UPDATE {table} d SET
            effective_end_date = t.source_updated_at, is_current = FALSE,
            run_id = :run_id, updated_at = now()
        FROM {stage} t
        WHERE d.{bk} = t.{bk} AND d.is_current
          AND {tup('d', t2)} IS DISTINCT FROM {tup('t', t2)}
          AND t.source_updated_at > d.effective_start_date
          AND t.source_updated_at >= d.source_updated_at
        RETURNING d.{bk}
    """), {"run_id": run_id}).scalars())

    inserted = conn.execute(text(f"""
        INSERT INTO {table} ({bk}, {", ".join(attrs)}, effective_start_date, effective_end_date,
                             is_current, source_updated_at, run_id)
        SELECT t.{bk}, {", ".join(f"t.{c}" for c in attrs)},
               CASE WHEN EXISTS (SELECT 1 FROM {table} h WHERE h.{bk} = t.{bk})
                    THEN t.source_updated_at ELSE TIMESTAMP '1900-01-01 00:00:00' END,
               TIMESTAMP '9999-12-31 00:00:00', TRUE, t.source_updated_at, :run_id
        FROM {stage} t
        WHERE NOT EXISTS (SELECT 1 FROM {table} d WHERE d.{bk} = t.{bk} AND d.is_current)
        RETURNING {bk}, effective_start_date
    """), {"run_id": run_id}).all()
    stats.new_members = sum(1 for _, start in inserted if start.year == 1900)
    stats.new_versions = len(inserted) - stats.new_members
    stats.changed_business_keys = sorted(expired)
    if stats.new_versions != len(expired):
        raise DataIntegrityError(
            f"{kind}: expired {len(expired)} version(s) but opened {stats.new_versions}")
    log.info("dim_%s: new=%d new_versions(SCD2)=%d type1_updates=%d stale_ignored=%d",
             kind, stats.new_members, stats.new_versions, stats.type1_updates, stats.stale_ignored)
    return stats


def load_dim_customer(conn: Connection, customers: pd.DataFrame, run_id: str) -> DimensionLoadStats:
    """SCD2 merge of the customer dimension (Type 2: city/state/country)."""
    return _load_scd2_dimension(conn, "customer", customers, run_id)


def load_dim_product(conn: Connection, products: pd.DataFrame, run_id: str) -> DimensionLoadStats:
    """SCD2 merge of the product dimension (Type 2: category/brand)."""
    return _load_scd2_dimension(conn, "product", products, run_id)


def load_dim_date(conn: Connection, dates: pd.DataFrame) -> int:
    """Insert missing calendar days. Existing days are never modified. Returns #inserted."""
    if dates.empty:
        return 0
    cols = ["date_key", "full_date", "day", "day_name", "week", "month", "month_name",
            "quarter", "year", "is_weekend"]
    _create_temp(conn, "tmp_dim_date_in",
                 "date_key integer, full_date date, day smallint, day_name varchar(9), "
                 "week smallint, month smallint, month_name varchar(9), quarter smallint, "
                 "year smallint, is_weekend boolean")
    copy_rows(conn, "tmp_dim_date_in", cols, frame_rows(dates, cols))
    return int(conn.execute(text(f"""
        WITH ins AS (
            INSERT INTO dw.dim_date ({", ".join(cols)})
            SELECT {", ".join(cols)} FROM tmp_dim_date_in
            ON CONFLICT (date_key) DO NOTHING
            RETURNING 1)
        SELECT count(*) FROM ins
    """)).scalar_one())


def fetch_dimension_versions(conn: Connection, kind: str, business_ids: set[str]) -> pd.DataFrame:
    """All versions of the requested keys: key, business_id, start, end (NaT = open)."""
    cfg = _SCD_CONFIG[kind]
    columns = ["key", "business_id", "effective_start_date", "effective_end_date"]
    if not business_ids:
        return pd.DataFrame(columns=columns)
    rows = conn.execute(text(f"""
        SELECT {cfg['key']}, {cfg['bk']}, effective_start_date,
               CASE WHEN is_current THEN NULL ELSE effective_end_date END
        FROM {cfg['table']} WHERE {cfg['bk']} = ANY(CAST(:ids AS text[]))
    """), {"ids": sorted(business_ids)}).all()
    return pd.DataFrame(rows, columns=columns)


# ----------------------------------------------------------------------------- facts
FACT_STAGE_COLUMNS = ["order_item_id", "order_id", "customer_key", "product_key", "date_key",
                      "order_timestamp", "order_status", "payment_method", "quantity",
                      "unit_price", "gross_sales", "discount_amount", "net_revenue", "source_file"]


def upsert_fact_sales(conn: Connection, facts: pd.DataFrame, run_id: str,
                      source_system: str) -> tuple[int, int, int]:
    """Idempotent fact load keyed on (source_system, order_item_id).

    * new event                      -> INSERT
    * same event, same content hash  -> no-op (re-run / re-delivery)
    * same event, different content  -> UPDATE in place (source correction)

    Returns ``(inserted, updated, unchanged)``.
    """
    if facts.empty:
        return 0, 0, 0
    _create_temp(conn, "tmp_fact_sales_in",
                 "order_item_id varchar(30), order_id varchar(30), customer_key bigint, "
                 "product_key bigint, date_key integer, order_timestamp timestamp, "
                 "order_status varchar(20), payment_method varchar(30), quantity integer, "
                 "unit_price numeric(12,2), gross_sales numeric(14,2), "
                 "discount_amount numeric(12,2), net_revenue numeric(14,2), source_file text")
    copy_rows(conn, "tmp_fact_sales_in", FACT_STAGE_COLUMNS,
              frame_rows(facts, FACT_STAGE_COLUMNS))
    inserted, updated = _upsert_counts(conn, """
        WITH up AS (
            INSERT INTO dw.fact_sales AS f (source_system, order_item_id, order_id, customer_key,
                product_key, date_key, order_timestamp, order_status, payment_method, quantity,
                unit_price, gross_sales, discount_amount, net_revenue, record_hash, source_file,
                run_id)
            SELECT :source_system, t.order_item_id, t.order_id, t.customer_key, t.product_key,
                   t.date_key, t.order_timestamp, t.order_status, t.payment_method, t.quantity,
                   t.unit_price, t.gross_sales, t.discount_amount, t.net_revenue,
                   dw.sales_record_hash(t.customer_key, t.product_key, t.date_key, t.order_id,
                       t.order_timestamp, t.order_status, t.payment_method, t.quantity,
                       t.unit_price, t.discount_amount),
                   t.source_file, :run_id
            FROM tmp_fact_sales_in t
            ON CONFLICT (source_system, order_item_id) DO UPDATE SET
                order_id = EXCLUDED.order_id, customer_key = EXCLUDED.customer_key,
                product_key = EXCLUDED.product_key, date_key = EXCLUDED.date_key,
                order_timestamp = EXCLUDED.order_timestamp,
                order_status = EXCLUDED.order_status, payment_method = EXCLUDED.payment_method,
                quantity = EXCLUDED.quantity, unit_price = EXCLUDED.unit_price,
                gross_sales = EXCLUDED.gross_sales, discount_amount = EXCLUDED.discount_amount,
                net_revenue = EXCLUDED.net_revenue, record_hash = EXCLUDED.record_hash,
                source_file = EXCLUDED.source_file, run_id = EXCLUDED.run_id, updated_at = now()
            WHERE f.record_hash IS DISTINCT FROM EXCLUDED.record_hash
            RETURNING (xmax = 0) AS inserted)
        SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
    """, {"source_system": source_system, "run_id": run_id})
    unchanged = len(facts) - inserted - updated
    log.info("fact_sales: inserted=%d updated=%d unchanged=%d", inserted, updated, unchanged)
    return inserted, updated, unchanged


def realign_fact_keys(conn: Connection, kind: str, business_ids: list[str], run_id: str) -> int:
    """Re-point existing facts to the dimension version valid at their order timestamp.

    Needed when an SCD2 change arrives *late* (its effective date is earlier than facts
    already loaded). Only facts of the changed business keys are examined.
    """
    if not business_ids:
        return 0
    cfg = _SCD_CONFIG[kind]
    table, key, bk = cfg["table"], cfg["key"], cfg["bk"]
    other_key = "product_key" if kind == "customer" else "customer_key"
    new_key_expr = f"v.{key}"
    hash_args = {"customer_key": "f.customer_key", "product_key": "f.product_key"}
    hash_args[key] = new_key_expr
    count = conn.execute(text(f"""
        UPDATE dw.fact_sales f SET
            {key} = v.{key},
            record_hash = dw.sales_record_hash({hash_args['customer_key']},
                {hash_args['product_key']}, f.date_key, f.order_id, f.order_timestamp,
                f.order_status, f.payment_method, f.quantity, f.unit_price, f.discount_amount),
            run_id = :run_id, updated_at = now()
        FROM {table} old_v
        JOIN {table} v ON v.{bk} = old_v.{bk}
        WHERE f.{key} = old_v.{key}
          AND old_v.{bk} = ANY(CAST(:ids AS text[]))
          AND f.order_timestamp >= v.effective_start_date
          AND f.order_timestamp <  v.effective_end_date
          AND v.{key} <> f.{key}
    """), {"ids": business_ids, "run_id": run_id}).rowcount
    if count:
        log.warning("Re-keyed %d existing fact row(s) after late %s change (%s unchanged)",
                    count, kind, other_key)
    return int(count or 0)
