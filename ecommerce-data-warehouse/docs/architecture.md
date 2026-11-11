# Architecture

## 1. End-to-end flow

```
 SOURCE SYSTEM (CSV extracts)
   customers.csv  products.csv  orders.csv  order_items.csv
        |
        v
 [1] INGESTION  src/ingestion/extract.py
     file exists / .csv / not empty / header check / schema drift
     every record kept as text + line number, checksum, ingested_at
        |
        v
 [2] VALIDATION  src/validation/{schemas,validate}.py
     Pydantic row schema (chunks of BATCH_SIZE) + vectorised business rules:
     duplicates (exact vs conflicting), e-mail uniqueness, references,
     discount <= gross, timestamp ordering
        |                                   \
        | valid records                      \ invalid records (all reasons)
        v                                     v
 [3] RAW LANDING + QUARANTINE  -- own transaction, COMMITTED --
     raw.customers / raw.products / raw.orders / raw.order_items
     (status VALID | REJECTED | SKIPPED)    ops.rejected_records (JSONB raw_record)
     ops.source_files (sha256, size, count)
        |
        v
 [4] TRANSFORMATION  src/transformation/{dimensions,facts}.py
     clean text, defaults for optional fields, typed timestamps, Decimal money
        |
        v
 [5] WAREHOUSE LOAD  -- ONE transaction + advisory lock, all-or-nothing --
     a. source.* upserts (ODS; newer updated_at wins)
     b. dw.dim_customer / dw.dim_product  SCD Type 2 merge
     c. fact build (incl. lines of orders whose header changed)
     d. dw.dim_date (missing days only)
     e. point-in-time surrogate-key resolution (merge_asof on event timestamp)
     f. dw.fact_sales upsert ON CONFLICT (source_system, order_item_id)
     g. re-key existing facts after late SCD2 changes
        |
        v
 [6] RECONCILIATION  src/reconciliation/reconcile.py
     counts, totals, missing refs, point-in-time integrity -> CSV + summary
        |
        v
 [7] RUN STATUS + METRICS  ops.pipeline_runs / ops.pipeline_metrics / logs/pipeline.log
     SUCCESS only if every CRITICAL reconciliation check passes
        |
        v
 [8] ANALYTICS  sql/analytical_queries.sql  (python -m src.cli queries)
```

One command runs the whole chain: `python -m src.cli run --input <dir>`.

## 2. Database layers (one PostgreSQL database, four schemas)

| Schema   | Purpose | Written by | Mutability |
|----------|---------|------------|------------|
| `raw`    | Every input record as text, per run, with validation status | landing txn | append-only |
| `source` | Typed transactional replica (ODS) with PK/FK/unique/check constraints | warehouse txn | upsert |
| `dw`     | Star schema: `dim_customer`, `dim_product`, `dim_date`, `fact_sales` | warehouse txn | SCD2 / upsert |
| `ops`    | `pipeline_runs`, `pipeline_metrics`, `rejected_records`, `source_files` | own small txns | append / update run row |

## 3. Source ER diagram

```
 customers (customer_id PK, email UNIQUE)
     | 1
     |               products (product_id PK)
     | N                 | 1
 orders (order_id PK,    |
         customer_id FK) |
     | 1                 |
     | N                 | N
 order_items (order_item_id PK, order_id FK, product_id FK)
```

## 4. Star schema

```
                       dw.dim_date
                       date_key PK (YYYYMMDD)
                             |
 dw.dim_customer             |             dw.dim_product
 customer_key PK  ----<  dw.fact_sales  >----  product_key PK
 customer_id (BK)        sales_key PK          product_id (BK)
 city/state/country      (source_system,       category/brand  (SCD2)
   (SCD2)                 order_item_id) UQ    product_name/unit_price (Type 1)
 name/email (Type 1)     order_id (degenerate)
 effective_start/end     order_status, payment_method
 is_current              quantity, unit_price, gross_sales,
                         discount_amount, net_revenue
                         record_hash, source_file, run_id, loaded_at
```

**Grain:** one row per product line within one customer order (one `order_items` row).

## 5. Transaction and failure model

| Step | Transaction | On failure |
|------|-------------|------------|
| `start_run` | own, committed | run cannot start (e.g. DB down, duplicate run id) -> exception |
| extract / validate | none (in memory) | run closed as FAILED with error message |
| raw + quarantine landing | own, committed | rolled back; run FAILED |
| warehouse load (source + dims + facts) | single, committed only at end | **whole batch rolled back**; raw + rejects + run row remain |
| reconciliation | read-only | critical failure -> status RECONCILIATION_FAILED |
| `finish_run` + metrics | own, committed | logged at ERROR and re-raised |

Recovery = re-run. All warehouse writes are idempotent upserts keyed on business/event
keys, so a retry converges to exactly the state a single successful run would produce
(`tests/test_pipeline.py::test_retry_after_failure`).

Concurrent runs are serialised with `pg_advisory_xact_lock` inside the warehouse
transaction.

## 6. Module map

| Module | Responsibility |
|--------|----------------|
| `src/config/settings.py` | env/.env configuration (Pydantic-validated) |
| `src/ingestion/extract.py` | `extract_customers/products/orders/order_items/all` |
| `src/validation/schemas.py` | column contracts, Pydantic record models, reason codes |
| `src/validation/validate.py` | rule engine, duplicate policy, quarantine records |
| `src/transformation/dimensions.py` | `transform_customers`, `transform_products`, `generate_date_dimension` |
| `src/transformation/facts.py` | `transform_sales`, `calculate_measures`, `resolve_surrogate_keys` |
| `src/loading/database.py` | engine, schema init, COPY helper, isolated DB helpers |
| `src/loading/load.py` | landing, ODS upserts, SCD2 merge, date dim, fact upsert, re-key |
| `src/monitoring/{logger,metrics}.py` | logging, stage timer, run rows, metrics |
| `src/reconciliation/reconcile.py` | source-vs-target checks and reports |
| `src/pipeline.py` | orchestration and transaction boundaries |
| `src/cli.py` | command-line interface |
| `src/generators/generate_data.py` | synthetic data (clean + invalid) |
| `src/benchmark.py`, `src/scenarios.py`, `src/analytics.py` | benchmark, scenario demo, named queries |
