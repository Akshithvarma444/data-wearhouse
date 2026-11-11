# Interview Notes - E-Commerce Sales Data Warehouse

**What problem does this project solve?**
The order system is built for taking orders, not for answering "revenue by city and
month over three years". I extract its four tables, validate them, quarantine bad
records, and load a PostgreSQL star schema that answers those questions quickly and
historically correctly. Every run is logged, reconciled and safe to repeat.

**What is dimensional modelling?**
Organising data around business processes: numeric **facts** (what was measured: quantity,
revenue) surrounded by descriptive **dimensions** (who, what, when: customer, product,
date). It optimises for reading and aggregating, not for updating single rows.

**What is a star schema?**
One central fact table joined directly to denormalised dimension tables (one hop each).
Here: `fact_sales` -> `dim_customer`, `dim_product`, `dim_date`. Joins are many-to-one on
surrogate keys, so they never multiply rows.

**What is fact grain?**
The exact meaning of one fact row. Mine is "one product line within one customer order"
(one `order_items` row) - the most detailed level available, so every metric can be
rolled up from it. Order-level metrics (AOV) aggregate lines by `order_id` first.

**What are surrogate keys?**
Warehouse-generated integer keys (`customer_key`) that are independent of source keys.
They are required for SCD2 (one customer, several versions), protect against source key
changes and make joins compact. The business key (`customer_id`) is kept for lineage.

**Fact vs dimension tables?**
Facts are narrow, long, mostly numeric and additive (quantity, gross, discount, net).
Dimensions are wide, short, descriptive and used to filter/group (city, category, month).

**What is SCD Type 2?**
Keeping history of dimension changes by versioning rows. When customer C1 moves from
Hyderabad to Chennai on 1 June, the Hyderabad row gets `effective_end_date = 2024-06-01`,
`is_current = false`, and a new Chennai row starts at 2024-06-01. Sales are linked to the
version valid **at the order timestamp**, so March sales stay in Hyderabad. A partial
unique index allows only one current row; an exclusion constraint forbids overlaps.
Name/e-mail are Type 1 (corrections overwrite).

**How does the ETL pipeline work?**
Extract CSVs (structure checks) -> validate (Pydantic row schema + vectorised business
rules) -> commit raw records and quarantine -> transform (clean, type, defaults) -> one
transaction: upsert source tables, SCD2 dimensions, date dimension, build facts, resolve
keys point-in-time, upsert facts -> reconcile -> record status and metrics. One command:
`python -m src.cli run --input data/raw`.

**How do you handle duplicates?**
Exact duplicates: keep the first row, quarantine the copies. Conflicting duplicates
(same key, different content): quarantine all copies - never guess - and dependants fail
their reference check. Across runs, the fact table's unique event key
`(source_system, order_item_id)` makes duplicates impossible.

**How do you achieve idempotency?**
Stable event keys with UNIQUE constraints and `INSERT ... ON CONFLICT DO UPDATE ... WHERE
record_hash IS DISTINCT FROM ...`: identical re-delivery is a no-op, a correction updates
in place. Dimensions compare attributes before versioning; source tables only accept a
newer `updated_at`. Proven by tests: running the same files twice leaves counts and totals
unchanged (benchmark re-runs: 0 inserted, all unchanged).

**How do you handle failures?**
The run row and the raw/quarantine data are committed first. The warehouse load is a
single transaction - on any error PostgreSQL rolls back source, dimensions and facts
together, the run is marked FAILED with the error and failed count. Recovery is simply
re-running; idempotent upserts make the retry converge. An advisory lock prevents
overlapping loads. Tested by injecting a failure into the fact load.

**How do you validate data quality?**
Schema rules (required, types, timestamp/number format, allowed values, lengths),
business rules (unique keys and e-mails, references, quantity > 0, prices >= 0, discount
<= gross, updated >= created) with stable reason codes. All reasons per record go to
`ops.rejected_records` with the original values as JSONB, file name and line number.

**How do you prove source/target reconciliation?**
After each run: per entity input = valid + rejected (+ skipped); rejected rows = quarantine
rows; valid order lines = fact rows present; gross, discount and net totals equal to the
paisa (NUMERIC); zero missing dimension references; zero facts linked to the wrong SCD
version; all-time source vs fact counts and revenue. CRITICAL failures make the run
RECONCILIATION_FAILED. Output: `reports/reconciliation_report.csv`.

**How did you measure performance?**
`python -m src.cli benchmark` generates 1K/10K/100K (optionally 1M) order lines, loads each
into a fresh isolated database, re-runs it to prove idempotency, and records real stage
timings and rows/second in `reports/performance_report.txt`. It also measures row-by-row
INSERT vs COPY on the same rows - COPY plus one set-based upsert is the optimisation
the pipeline uses. Profiling then removed two validation hot spots (Python-level
Decimal checks, `DataFrame.to_dict`) and roughly halved validation time.
