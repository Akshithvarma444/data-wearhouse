# Architecture

## 0. Application architecture

```
 Browser (React + TypeScript + Vite + Tailwind + Recharts)          frontend/
   pages: Dashboard, Data Sources, Data Generator, Pipeline, Data Quality,
          Data Warehouse, Analytics, Pipeline History, Reports, Settings
   src/api/client.ts  - one HTTP client, base URL from VITE_API_BASE_URL
        |
        |  JSON over HTTP  (dev: CORS to :8000 / Docker: nginx proxies /api -> backend:8000)
        v
 FastAPI  (backend/app/main.py, routes in backend/app/api/routes/)
   /api/health  /api/settings/config  /api/dashboard  /api/datasets  /api/generator
   /api/pipeline  /api/quality  /api/warehouse  /api/analytics  /api/reports  /api/history
        |                                   |
        | services (backend/app/services)   | run_tracker: POST /api/pipeline/run starts the real
        |   reporting, datasets,            | pipeline in a worker thread; StageTimer reports each
        |   generator_service, pipeline ... | stage start/end -> GET /api/pipeline/status/{run_id}
        v                                   v
 PostgreSQL 18:  raw.*  source.*  dw.*  ops.*        files: other/datasets, other/reports
```

* The frontend never computes business numbers itself; every figure is an API
  response read from PostgreSQL (or from report files written by real runs).
* Pipeline progress is real: the API's run tracker receives stage events from the
  pipeline's `StageTimer`; the browser polls `/api/pipeline/status/{run_id}` once per
  second. For runs that finished earlier (or were started from the CLI) stage timings are
  rebuilt from `ops.pipeline_metrics`.
* Only one pipeline run is started at a time through the API (HTTP 409 otherwise); a
  PostgreSQL advisory lock also serialises warehouse loads across processes.
* Uploads are written to `other/datasets/uploads/<entity>.csv` (entity whitelist, `.csv`
  only, size limit, header check, atomic rename) and loaded with source = `uploads`.

### API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /api/health | API + database connectivity |
| GET | /api/settings/config | non-secret runtime configuration |
| GET | /api/dashboard/summary | headline figures, recent runs, trend, categories, quality |
| GET | /api/datasets | the four files of every dataset source, counts and schema check |
| POST | /api/datasets/upload | upload one entity CSV (multipart: entity, file) |
| POST | /api/generator/generate | generate synthetic data (customers, products, orders, seed, include_invalid) |
| GET | /api/generator/download?dataset=generated\|invalid | dataset as .zip |
| POST | /api/pipeline/run | start the ETL (source = generated \| uploads \| sample \| invalid) |
| GET | /api/pipeline/status/{run_id} | live stage progress and counts |
| GET | /api/pipeline/history | recent runs (also /api/history, /api/history/{run_id}) |
| GET | /api/quality/summary | valid/rejected, missing, duplicates, references, schema, business |
| GET | /api/quality/rejections | quarantined records (filters: entity, reason, run_id; paging) |
| GET | /api/warehouse/tables | catalog with row counts |
| GET | /api/warehouse/tables/{table_name} | columns, types, PK/FK, sample rows |
| GET | /api/analytics/revenue | totals, AOV, yearly, status, city, payment |
| GET | /api/analytics/monthly-sales, /top-products, /top-customers, /category-sales | analytical queries |
| GET | /api/reports/reconciliation | reconciliation checks (latest or given run) |
| GET | /api/reports/performance | benchmark results + recent run timings |
| GET | /api/reports/export?report=...&format=csv\|json | downloads (reconciliation, quality, rejections, performance, history) |

Interactive documentation: `http://localhost:8000/docs` (OpenAPI).


## 1. End-to-end flow

```
 SOURCE SYSTEM (CSV extracts)
   customers.csv  products.csv  orders.csv  order_items.csv
        |
        v
 [1] INGESTION  backend/app/services/ingestion/extract.py
     file exists / .csv / not empty / header check / schema drift
     every record kept as text + line number, checksum, ingested_at
        |
        v
 [2] VALIDATION  backend/app/services/validation/{schemas,validate}.py
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
 [4] TRANSFORMATION  backend/app/services/transformation/{dimensions,facts}.py
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
 [6] RECONCILIATION  backend/app/services/reconciliation/reconcile.py
     counts, totals, missing refs, point-in-time integrity -> CSV + summary
        |
        v
 [7] RUN STATUS + METRICS  ops.pipeline_runs / ops.pipeline_metrics / backend/logs/pipeline.log
     SUCCESS only if every CRITICAL reconciliation check passes
        |
        v
 [8] ANALYTICS  other/sql/analytical_queries.sql  (python -m app.cli queries)
```

One command runs the whole chain: `python -m app.cli run --input <dir>`.

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
(`backend/tests/test_pipeline.py::test_retry_after_failure`).

Concurrent runs are serialised with `pg_advisory_xact_lock` inside the warehouse
transaction.

## 6. Module map (backend/app)

| Module | Responsibility |
|--------|----------------|
| `backend/app/config/settings.py` | env/.env configuration (Pydantic-validated) |
| `backend/app/services/ingestion/extract.py` | `extract_customers/products/orders/order_items/all` |
| `backend/app/services/validation/schemas.py` | column contracts, Pydantic record models, reason codes |
| `backend/app/services/validation/validate.py` | rule engine, duplicate policy, quarantine records |
| `backend/app/services/transformation/dimensions.py` | `transform_customers`, `transform_products`, `generate_date_dimension` |
| `backend/app/services/transformation/facts.py` | `transform_sales`, `calculate_measures`, `resolve_surrogate_keys` |
| `backend/app/database/connection.py` | engine, schema init, COPY helper, isolated DB helpers |
| `backend/app/services/loading/load.py` | landing, ODS upserts, SCD2 merge, date dim, fact upsert, re-key |
| `backend/app/services/monitoring/{logger,metrics}.py` | logging, stage timer, run rows, metrics |
| `backend/app/services/reconciliation/reconcile.py` | source-vs-target checks and reports |
| `backend/app/services/pipeline.py` | orchestration and transaction boundaries |
| `backend/app/cli.py` | command-line interface |
| `backend/app/generators/generate_data.py` | synthetic data (clean + invalid) |
| `backend/app/services/{benchmark,scenarios}.py`, `backend/app/services/analytics/queries.py` | benchmark, scenario demo, named queries |
