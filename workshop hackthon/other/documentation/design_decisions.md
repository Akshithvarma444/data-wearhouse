# Design Decisions and Trade-offs

## Transactional database vs analytical warehouse

| | OLTP source (orders system) | Analytical warehouse (this project) |
|---|---|---|
| Optimised for | many small inserts/updates, one order at a time | large scans and aggregations |
| Model | normalised (3NF): customers, orders, order_items, products | denormalised star schema |
| History | current state only (an address change overwrites the old one) | full history (SCD Type 2) |
| Typical query | "show order O123" | "revenue by city and month for 3 years" |
| Keys | natural/business keys | surrogate keys + business keys |
| Load pattern | continuous user transactions | batch ETL, idempotent, reconciled |

Running "monthly revenue by category" on the OLTP model needs 4-way joins over the
largest tables and gives *current* category/city instead of the value at the time of the
sale. The warehouse answers it with one fact table and small dimensions, historically
correct.

## Why a dimensional model / star schema

* Business users think in **facts** (sales) and **dimensions** (who, what, when). The model
  mirrors those questions directly.
* A star (not snowflake) keeps every analytical query to one join hop per dimension:
  fact -> dimension, always many-to-one on a surrogate key, so joins never multiply rows
  and revenue is never double counted (`backend/tests/test_analytics.py`).
* Dimensions are small (thousands of rows); denormalising city/state into `dim_customer`
  costs nothing and removes joins.

## Why this fact grain

One row per **order line** is the most atomic level the source provides. Every
required metric can be rolled up from it (product revenue, category revenue, order
counts, AOV), whereas an order-level grain would lose product detail. Order-level
facts (AOV, status counts) are derived by aggregating lines to `order_id` (degenerate
dimension) first.

## Why surrogate keys

* SCD2 needs several rows per business key - the business key alone cannot be the PK.
* They isolate the warehouse from source key changes / reuse and from multiple sources.
* Integer joins are compact and fast.
Business keys are kept in every dimension for lineage and lookups.

## Why SCD Type 2 (and which attributes)

Revenue by city must reflect where the customer lived **when they bought**. Type 2 is
applied only to attributes with analytical meaning over time:

| Dimension | Type 2 (history) | Type 1 (overwrite) |
|-----------|------------------|--------------------|
| customer | city, state, country | first_name, last_name, email (corrections) |
| product | category, brand | product_name, unit_price (list price; the paid price is on the fact) |

Rules:
* Effective ranges are half-open `[start, end)`; a change at `T` closes the old version at
  `T` and opens the new one at `T` - no gaps, no overlaps (gist exclusion constraint).
* The first version starts at `1900-01-01`, so any fact dated before the first recorded
  change still resolves (no "unknown" member needed).
* The change timestamp is the **source `updated_at`**, not the load time - loading late does
  not shift history.
* Out-of-order (older) source updates are ignored and counted (`*_stale_ignored`).
* Facts resolve keys **by event timestamp**, never by `is_current`.
* **Late-arriving dimension changes**: when a new version starts before facts that are
  already loaded, those facts are re-pointed to the right version (`realign_fact_keys`,
  metric `facts_rekeyed`).

## Why PostgreSQL

Open source, transactional DDL, `ON CONFLICT` upserts, `COPY` bulk loading, partial
indexes, exclusion constraints (no overlapping SCD ranges), JSONB for quarantined raw
records, window functions / `ROLLUP` / `percentile_cont` for analytics. More than enough
for this volume on one machine.

## Why synthetic data

No dataset was provided. A seeded Faker (`en_IN`) + NumPy generator gives realistic
Indian names/cities, reproducible files (byte-identical for the same seed), any volume
(100 rows to 1M+), and a separate dataset with *known, catalogued* defects
(`other/datasets/invalid/injected_defects.csv`) so validation can be tested precisely.

## Validation and duplicate policy

* Pydantic models are the row-level contract (types, formats, allowed values); business
  and cross-record rules are vectorised pandas; all reasons for a record are collected.
* **Exact duplicates** (same key, identical content): keep the first row, reject later
  copies (`EXACT_DUPLICATE`) - nothing is silently dropped.
* **Conflicting duplicates** (same key, different content): reject **all** copies
  (`CONFLICTING_DUPLICATE`); the pipeline never guesses which one is right. Dependants
  then fail their reference check and are quarantined too.
* References may be satisfied by the batch or by data loaded earlier, so late-arriving
  orders/lines work.
* Missing dimension references are handled by **explicit rejection** (quarantine), not by
  an "unknown" member: no fact is ever published with a fake customer or product.

## Idempotency

| Mechanism | Where |
|-----------|-------|
| Stable event key `(source_system, order_item_id)` with UNIQUE constraint | `dw.fact_sales` |
| `INSERT ... ON CONFLICT DO UPDATE ... WHERE record_hash IS DISTINCT FROM` | fact load: identical re-delivery = no-op, correction = in-place update |
| Business-key upserts guarded by `updated_at` (newer wins) | `source.*` |
| SCD2 merge compares attributes; unchanged keys produce no rows | dimensions |
| `ON CONFLICT DO NOTHING` | `dim_date` |
| Unique `run_id`, file checksum in `ops.source_files` | traceability of every execution |

Running the same input twice leaves row counts and totals unchanged
(`backend/tests/test_idempotency.py`). A *corrected* source line (same `order_item_id`, new
content) updates the fact in place and records the new `run_id`/`updated_at`. A changed
order header (e.g. status -> RETURNED) rebuilds all lines of that order from the source
layer.

**Backfill**: `--from-date/--to-date` loads only orders in the window (others are marked
SKIPPED and explained in reconciliation); historical files can be replayed at any time
because keys are resolved by event date.

## Failure recovery

* Run row (RUNNING) is committed first; raw records + quarantine are committed before
  the warehouse load, so evidence survives failures.
* The warehouse load is **one transaction**: source upserts, dimensions, date dimension
  and facts commit together or not at all - a half-loaded fact batch can never be
  published (`test_database_failure_rolls_back_warehouse`).
* The run is closed as FAILED with the error message and `failed_count`.
* Recovery = re-run (new run id). Because every write is an idempotent upsert the retry
  converges to the correct state (`test_retry_after_failure`).
* An advisory lock prevents two loads from interleaving.
* A run whose critical reconciliation checks fail is **not** reported as SUCCESS
  (status `RECONCILIATION_FAILED`).

## Why bulk inserts (COPY + set-based SQL)

Row-by-row INSERTs pay a network round trip and statement execution per row. The
pipeline streams rows with PostgreSQL `COPY` into a temporary staging table and then
runs **one** set-based `INSERT ... SELECT ... ON CONFLICT` per table. The benchmark
measures both approaches on the same rows (see `other/reports/performance_report.txt`).

## Limitations

* Single-node, in-memory pandas processing: very large files (tens of millions of rows)
  would need chunked processing end to end.
* Validation reads the referenced keys from the source layer per run; with very large
  histories this lookup would move into SQL.
* Raw landing keeps every run's records (no retention policy implemented).
* `order_items` corrections are accepted in arrival order (the file has no
  `updated_at`); an older re-delivery overwrites a newer correction.
* Type 1 attributes overwrite every version (no "as-was" name/e-mail history).
* Two changes for the same key with an identical `updated_at` cannot be ordered; the
  second is ignored.
* One source system only (the event key already includes `source_system`).

## Production improvements

* Orchestrate with a scheduler (Airflow/Dagster) and alert on FAILED /
  RECONCILIATION_FAILED runs.
* Partition `fact_sales` by month and `raw.*` by run date; add retention for raw data.
* Incremental extraction (CDC / watermark on `updated_at`) instead of full files.
* Data contracts with the source team; schema registry for drift handling.
* Secrets in a vault; separate DB roles for loader vs. analysts (read-only).
* Data quality dashboards over `ops.pipeline_metrics` and `ops.rejected_records`.
* Materialised aggregates (monthly revenue) for BI tools.
