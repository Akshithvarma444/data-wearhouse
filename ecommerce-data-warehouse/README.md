# E-Commerce Sales Data Warehouse

An end-to-end, production-minded ETL pipeline that turns transactional e-commerce data
(customers, products, orders, order items) into a PostgreSQL **star schema** with
**SCD Type 2** history, a **quarantine** for invalid records, **idempotent** loads,
**reconciliation** reports, run **metrics**, a **benchmark** and **109 automated tests**.

Python 3.12 · PostgreSQL 18 · pandas · SQLAlchemy 2 · psycopg 3 · Pydantic 2 · Faker/NumPy · pytest · Docker

---

## 1. Project overview
One command (`python -m src.cli run --input <dir>`) extracts four CSV extracts, validates
every record, lands raw data and rejected records, transforms them into dimensions and
facts, publishes them atomically, reconciles source against target and records the
outcome in operational tables.

## 2. Business problem
Management wants revenue by month/year, best-selling products, top categories, city-wise
revenue, customer behaviour, order trends, AOV and growth. The transactional schema is
normalised for order taking: these questions need multi-way joins over the largest tables
and lose history (a customer's *current* city replaces the city where they actually
bought). A dimensional warehouse answers them simply and historically correctly.

## 3. Objectives
* Dimensional model (star schema) with documented grain, surrogate keys and measures.
* Reproducible synthetic data (100 rows to 1M+ order lines), separate invalid test data.
* Validation with quarantine and traceable reasons; deterministic duplicate policy.
* SCD Type 2 for customers and products; point-in-time key resolution; late data.
* Idempotent, transactional, observable, reconciled loads; safe retry.
* Analytical SQL, automated tests, and measured performance.

## 4. Technologies
| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Database | PostgreSQL 18 (Docker image `postgres:18`) |
| Processing | pandas 3 (+ pyarrow string backend), NumPy |
| DB access | SQLAlchemy 2 + psycopg 3 (COPY for bulk loads) |
| Validation | Pydantic 2 |
| Data generation | Faker (`en_IN`) + NumPy |
| Config | python-dotenv |
| Tests | pytest |
| Containers | Docker / Docker Compose |

## 5. Architecture
```
CSV files -> INGESTION -> VALIDATION -+-> valid ----------------------------+
                                      +-> invalid -> ops.rejected_records   |
             raw.* landing (all records, committed)                         |
                                                                            v
             TRANSFORMATION -> [one transaction] source.* -> SCD2 dims -> dim_date
                               -> facts (point-in-time keys) -> fact_sales upsert
             -> RECONCILIATION -> ops.pipeline_runs / pipeline_metrics -> ANALYTICS
```
Details, diagrams and transaction model: [docs/architecture.md](docs/architecture.md).

## 6. Data flow
1. **Extract** (`src/ingestion/extract.py`): file exists, `.csv`, not empty; required
   columns present (else run fails); optional columns may be absent; unexpected columns
   are warned about (or fail with `--strict-schema`); rows with a wrong field count are
   captured as malformed; checksum, size and record count recorded.
2. **Validate** (`src/validation`): Pydantic row schema + vectorised business rules.
3. **Land** raw records (`raw.*`, status VALID/REJECTED/SKIPPED) and quarantine
   (`ops.rejected_records`) in their own committed transaction.
4. **Transform** (`src/transformation`): trim/collapse whitespace, normalise case,
   defaults for optional fields, exact `Decimal` money.
5. **Load** in one transaction: `source.*` upserts -> `dim_customer`/`dim_product` SCD2 ->
   `dim_date` -> fact build -> point-in-time surrogate keys -> `fact_sales` upsert ->
   re-key facts affected by late dimension changes.
6. **Reconcile** and close the run (SUCCESS only if every critical check passes).

## 7. Source schema
`sql/source_schema.sql` - `source.customers`, `source.products`, `source.orders`,
`source.order_items` with PKs, FKs (orders->customers, items->orders, items->products),
unique e-mail, check constraints (price >= 0, quantity > 0, discount <= gross, allowed
statuses / payment methods, updated >= created), FK indexes in `sql/indexes.sql`.
Column-level documentation: [docs/data_dictionary.md](docs/data_dictionary.md).

## 8. Target dimensional model
```
             dim_date (date_key YYYYMMDD)
                        |
dim_customer ---< fact_sales >--- dim_product
(SCD2)          grain: order line     (SCD2)
```
`sql/warehouse_schema.sql`.

## 9. Fact grain
**One row of `dw.fact_sales` = one product line within one customer order**
(one `order_items` record). Event key: `(source_system, order_item_id)` (UNIQUE).

## 10. Dimensions
| Dimension | Business key | SCD2 attributes | Type 1 attributes |
|---|---|---|---|
| `dim_customer` | customer_id | city, state, country | first_name, last_name, email |
| `dim_product` | product_id | category, brand | product_name, unit_price (list price) |
| `dim_date` | full_date | - (static calendar: day, day_name, ISO week, month, quarter, year, is_weekend) | |
Degenerate dimensions on the fact: `order_id`, `order_status`, `payment_method`.

## 11. Surrogate keys
`customer_key`, `product_key`, `sales_key` are `BIGINT GENERATED ALWAYS AS IDENTITY`;
`date_key` is the integer `YYYYMMDD`. Facts store surrogate keys resolved **by order
timestamp** against the dimension version's effective range (`pandas.merge_asof`).

## 12. Measures
| Measure | Rule | Additivity |
|---|---|---|
| quantity | source | additive |
| unit_price | source (price paid) | non-additive |
| gross_sales | `quantity * unit_price` | additive |
| discount_amount | source, 0 if missing | additive |
| net_revenue | `gross_sales - discount_amount` | additive |
Computed in integer paise (exact) and stored as `NUMERIC`; check constraints enforce both
formulas in the database. "Recognised revenue" in the analytical queries excludes
CANCELLED and RETURNED orders.

## 13. Data-quality rules
Customers: id present/unique, e-mail valid and unique across customers, timestamps valid,
`updated_at >= created_at`. Products: id present/unique, name not blank, price >= 0 with at
most 2 decimals. Orders: id unique, customer exists (batch or warehouse), valid timestamp,
status in {PLACED, SHIPPED, DELIVERED, CANCELLED, RETURNED}. Order items: id unique, order
and product exist, quantity integer > 0, price >= 0, discount >= 0 and <= gross.
Duplicates: **exact** -> keep first, quarantine copies; **conflicting** -> quarantine all.
Every reject stores file, line number, business key, all reason codes/messages and the
original record (JSONB). Codes are listed in the data dictionary.

## 14. SCD Type 2
* Change in a tracked attribute with a newer source `updated_at` -> old version gets
  `effective_end_date = updated_at`, `is_current = false`; new version starts at the same
  instant (`[start, end)` ranges, no gaps/overlaps - enforced by a gist exclusion constraint).
* First version starts at `1900-01-01`; current version ends `9999-12-31`; a partial unique
  index allows only one current row per business key.
* Older (out-of-order) updates are ignored and counted.
* Late dimension changes re-key already-loaded facts (`facts_rekeyed` metric).
Demonstration: `python -m src.cli demo-scenarios` -> `reports/scenario_demo.txt`.

## 15. Idempotency strategy
* Fact upsert on `(source_system, order_item_id)` with `record_hash` comparison:
  identical re-delivery = no-op, changed content = in-place correction (new `run_id`).
* `source.*` upserts accept only newer `updated_at`; dimensions version only real changes;
  `dim_date` uses `ON CONFLICT DO NOTHING`.
* Each execution has a unique `run_id`; file checksums are kept in `ops.source_files`.
* Running the same input twice leaves fact row count and every total unchanged.
* Order header changes (e.g. status -> RETURNED) rebuild all lines of that order.
* Backfill: `--from-date/--to-date` restricts the run to an order-date window.

## 16. Retry and recovery
Run row and raw/quarantine data are committed before the warehouse load; the warehouse
load is a single transaction (plus an advisory lock). On failure everything in it is
rolled back, the run is marked `FAILED` with `error_message` and `failed_count`, and the
raw and rejected records remain. Recovery = run again; idempotent upserts guarantee the
same end state. Runs whose critical reconciliation checks fail are marked
`RECONCILIATION_FAILED`, never `SUCCESS`.

## 17. Database setup

### Option A - Docker (recommended)
```powershell
Copy-Item .env.example .env          # then set DB_PASSWORD (and DB_PORT if 5432 is busy)
docker compose up -d                 # PostgreSQL 18, waits for healthy
docker compose ps
```
If you change `DB_PORT`/`DB_PASSWORD`, update `DATABASE_URL` in `.env` to match.

### Option B - native PostgreSQL 18 on Windows
Install PostgreSQL 18 from the EDB installer (includes the `btree_gist` contrib extension),
then in PowerShell:
```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE ecommerce_dw;"
Copy-Item .env.example .env          # set DATABASE_URL / DB_* to your server
```
The test, benchmark and demo databases (`ecommerce_dw_test`, `_bench`, `_demo`) are created
automatically on the same server.

## 18. Python environment setup (Windows PowerShell)
```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # only if activation is blocked
.\.venv\Scripts\Activate.ps1
python --version                      # Python 3.12.x
```

## 19. Dependency installation
```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 20. Dataset generation
```powershell
# small committed sample (already in data/sample): 40 customers, 25 products, 100 order lines
python -m src.generators.generate_data --customers 40 --products 25 --order-items 100 --seed 42 --output-dir data/sample
# intermediate dataset -> data/raw (10,000 orders, ~25,000 lines)
python -m src.generators.generate_data --customers 1000 --products 200 --orders 10000 --seed 42
# performance dataset, exact line count -> data/generated
python -m src.generators.generate_data --customers 10000 --products 1000 --order-items 100000 --output-dir data/generated
# deliberately invalid dataset + defect manifest -> data/invalid
python -m src.generators.generate_data --invalid
```
Same seed => byte-identical files. Clean and invalid data never mix.

## 21. Pipeline execution
**Everything in one command** (generate data → load → idempotent re-run → reconcile →
queries → scenarios → tests, optional benchmark; output in `reports/end_to_end_run.txt`
when redirected):
```powershell
.\run_all.ps1              # add -Benchmark for the 1K/10K/100K benchmark
```
Individual commands:
```powershell
python -m src.cli init-db                         # create schemas/tables/indexes (idempotent)
python -m src.cli run --input data/sample         # small sample
python -m src.cli run --input data/raw            # intermediate dataset
python -m src.cli run --input data/generated      # large dataset
python -m src.cli run --input data/invalid        # shows quarantine
python -m src.cli run --input data/raw --run-id my-run-001 --batch-size 10000
python -m src.cli run --input data/raw --from-date 2024-01-01 --to-date 2024-12-31   # backfill window
python -m src.cli run --input data/raw --strict-schema   # fail on unexpected columns
python -m src.cli runs                            # recent runs from ops.pipeline_runs
python -m src.cli reconcile [--run-id <id>]       # re-run reconciliation (default: latest)
python -m src.cli init-db --reset --yes           # DROP and recreate everything (asks for --yes)
```
Logs: console + `logs/pipeline.log`. Exit code 0 = SUCCESS, 1 = FAILED/RECONCILIATION_FAILED.

## 22. Automated testing
```powershell
pytest -v                       # 109 tests (needs PostgreSQL; uses <DB_NAME>_test)
pytest tests/test_validation.py -v   # unit tests only (no database needed)
```
Tests never touch the main database: they run in `ecommerce_dw_test` (or
`TEST_DATABASE_URL`) and the suite aborts if that name does not end in `_test`. Scenario
coverage: [docs/test_scenarios.md](docs/test_scenarios.md). Last full run:
`reports/test_results.txt`.

## 23. Analytical SQL examples
`sql/analytical_queries.sql` (13 named queries): total, monthly (MoM growth) and yearly
(YoY growth) revenue, top-10 products, category revenue, city revenue, top-10 customers,
average order value (aggregated per order first), quantity sold (ROLLUP), order status
analysis, revenue by payment method, historical customer sales per SCD2 version,
repeat-buyer segmentation.
```powershell
python -m src.cli queries                         # all queries
python -m src.cli queries --name monthly_revenue
```
```sql
-- Average order value: aggregate to order level first
WITH order_totals AS (
    SELECT order_id, sum(net_revenue) AS order_value
    FROM dw.fact_sales WHERE order_status NOT IN ('CANCELLED', 'RETURNED')
    GROUP BY order_id)
SELECT round(avg(order_value), 2) AS average_order_value FROM order_totals;
```
Expected results for the sample dataset are computed independently from the CSVs in
`tests/fixtures/expected_sample_results.json` and verified by `tests/test_analytics.py`.

### Dashboard (tables + charts)
```powershell
python -m src.cli dashboard --open      # writes and opens reports/dashboard.html
```
A single self-contained HTML page (no server, no CDN, works offline) with four tabs:
**Sales analytics** (KPI tiles, monthly revenue line, revenue by year / category / city /
payment method, order status, customer segments, top products and customers),
**Data quality** (rejections by reason and entity, latest reconciliation checks,
quarantined records), **Pipeline runs** (durations and full run history) and
**Performance** (benchmark throughput, stage breakdown, COPY vs INSERT). Every chart has a
Chart/Table toggle and hover tooltips; light and dark themes. Open a tab directly with
`dashboard.html#quality`, `#pipeline` or `#performance`. Re-run the command after a
pipeline run to refresh the snapshot. `run_all.ps1` builds it as its last step.

## 24. Reconciliation
Every run writes `reports/reconciliation_report.csv` (+ history in
`reports/reconciliation/` and `reports/reconciliation_summary.txt`). Checks: per entity
`input = valid + rejected + skipped`; rejected rows = quarantine rows; valid order lines =
fact rows present; gross/discount/net totals (exact); duplicates quarantined; facts
inserted/updated/unchanged (explains re-runs); missing dimension references = 0; facts on
the wrong SCD version or date = 0; all-time source vs fact count and revenue; single
current version per key.

## 25. Performance benchmarking
```powershell
python -m src.cli benchmark                      # 1K, 10K, 100K order lines
python -m src.cli benchmark --include-1m         # + 1,000,000
```
Runs in an isolated `<DB_NAME>_bench` database, measures generation, extract, validate,
transform, load, reconcile, total and rows/s, plus an idempotent re-run and a
row-by-row INSERT vs COPY experiment. Results: `reports/performance_report.txt`.

## 26. Expected outputs
| Artifact | Location |
|---|---|
| Run summary (counts, status, stage timings) | console + `logs/pipeline.log` |
| Run history and metrics | `ops.pipeline_runs`, `ops.pipeline_metrics` |
| Quarantined records | `ops.rejected_records` |
| Reconciliation | `reports/reconciliation_report.csv`, `reports/reconciliation_summary.txt` |
| Performance | `reports/performance_report.txt` |
| SCD2 / late data demo | `reports/scenario_demo.txt` |
| Evidence from the last verification run | `reports/test_results.txt`, `reports/sample_pipeline_run.txt`, `reports/invalid_pipeline_run.txt`, `reports/analytical_query_results.txt` |

## 27. Known limitations
Single-node in-memory processing (fine to ~1M lines per batch, not for 100M); raw landing
has no retention policy; order-item corrections apply in arrival order (no `updated_at` on
lines); Type 1 attributes keep no history; changes with identical timestamps cannot be
ordered; one source system. Full list in
[docs/design_decisions.md](docs/design_decisions.md#limitations).

## 28. Future improvements
Scheduler + alerting (Airflow/Dagster), incremental CDC extraction, monthly partitioning
of `fact_sales`, raw-data retention, read-only analyst role, data-quality dashboards over
`ops.*`, materialised monthly aggregates, chunked end-to-end processing for very large
files.

---

## Project layout
```
data/{sample,raw,generated,invalid}   sql/  src/  tests/  docs/  reports/  logs/
src/config/settings.py              environment configuration
src/ingestion/extract.py            extract_customers/products/orders/order_items/all
src/validation/{schemas,validate}.py  Pydantic contracts, rules, quarantine
src/transformation/{dimensions,facts}.py
src/loading/{database,load}.py      COPY, upserts, SCD2, facts
src/monitoring/{logger,metrics}.py  logging, run rows, metrics
src/reconciliation/reconcile.py     reconciliation reports
src/pipeline.py  src/cli.py          orchestration and CLI
src/generators/generate_data.py     synthetic data
src/benchmark.py src/scenarios.py src/analytics.py
```
Interview summary: [docs/interview_notes.md](docs/interview_notes.md) ·
Design trade-offs: [docs/design_decisions.md](docs/design_decisions.md)
