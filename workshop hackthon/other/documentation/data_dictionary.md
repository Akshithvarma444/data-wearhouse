# Data Dictionary

Conventions: money is `NUMERIC(12,2)` (line values) / `NUMERIC(14,2)` (extended values) and
is never stored or computed as floating point. Source timestamps use the format
`YYYY-MM-DD HH:MM:SS` (an ISO `T` separator is also accepted) and are stored as
`TIMESTAMP` (no time zone; the source system's local time). BK = business key.

## 1. Source files / `source` schema (ODS)

### customers (`customers.csv`, `source.customers`)

| Column | Type | Null | Rules | Description |
|--------|------|------|-------|-------------|
| customer_id | VARCHAR(20) | no | PK; unique in file | Source customer identifier (BK) |
| first_name | VARCHAR(100) | no | required | Given name |
| last_name | VARCHAR(100) | yes | optional -> NULL | Family name (not every customer has one) |
| email | VARCHAR(255) | no | valid format; UNIQUE across customers; lower-cased | Contact e-mail |
| city | VARCHAR(100) | no | required; ALL-CAPS/lower-case is title-cased | City of residence (SCD2 tracked) |
| state | VARCHAR(100) | no | required | Indian state / UT (SCD2 tracked) |
| country | VARCHAR(100) | no | optional in file -> default `India` | Country (SCD2 tracked) |
| created_at | TIMESTAMP | no | valid timestamp | Record creation in source |
| updated_at | TIMESTAMP | no | optional -> `created_at`; must be >= created_at | Last change in source; drives SCD2 effective date |
| last_run_id, last_source_file, loaded_at | lineage | no | set by pipeline | Run / file that last changed the row |

### products (`products.csv`, `source.products`)

| Column | Type | Null | Rules | Description |
|--------|------|------|-------|-------------|
| product_id | VARCHAR(20) | no | PK; unique in file | Source product identifier (BK) |
| product_name | VARCHAR(200) | no | required, not blank | Display name (Type 1 in dimension) |
| category | VARCHAR(100) | no | required | Merchandising category (SCD2 tracked) |
| brand | VARCHAR(100) | no | optional -> `Unbranded` | Brand (SCD2 tracked) |
| unit_price | NUMERIC(12,2) | no | >= 0, max 2 decimals, plain decimal text | Current list price (Type 1 in dimension) |
| created_at | TIMESTAMP | no | valid timestamp | Creation in source |
| updated_at | TIMESTAMP | no | optional -> `created_at`; >= created_at | Last change; drives SCD2 effective date |

### orders (`orders.csv`, `source.orders`)

| Column | Type | Null | Rules | Description |
|--------|------|------|-------|-------------|
| order_id | VARCHAR(30) | no | PK; unique in file | Order identifier (degenerate dimension in fact) |
| customer_id | VARCHAR(20) | no | FK -> customers (batch or already loaded) | Buyer |
| order_date | TIMESTAMP | no | valid timestamp | Event time - used for date key and SCD2 lookups |
| order_status | VARCHAR(20) | no | one of PLACED, SHIPPED, DELIVERED, CANCELLED, RETURNED (case-insensitive) | Order lifecycle status |
| payment_method | VARCHAR(30) | no | UPI, CREDIT_CARD, DEBIT_CARD, NET_BANKING, CASH_ON_DELIVERY, WALLET; optional -> `UNKNOWN` | Payment instrument |
| updated_at | TIMESTAMP | no | optional -> order_date; >= order_date | Last status change; newer wins |

### order_items (`order_items.csv`, `source.order_items`)

| Column | Type | Null | Rules | Description |
|--------|------|------|-------|-------------|
| order_item_id | VARCHAR(30) | no | PK; unique in file | Order line identifier (fact event key) |
| order_id | VARCHAR(30) | no | FK -> orders (batch or already loaded) | Parent order |
| product_id | VARCHAR(20) | no | FK -> products (batch or already loaded) | Product sold |
| quantity | INTEGER | no | integer > 0 | Units |
| unit_price | NUMERIC(12,2) | no | >= 0, <= 2 decimals | Selling price per unit on this line |
| discount_amount | NUMERIC(12,2) | no | >= 0 and <= quantity x unit_price; optional -> 0.00 | Line discount |
| created_at | TIMESTAMP | no | valid timestamp | Line creation time |

## 2. Warehouse (`dw` schema)

### dw.dim_customer (SCD Type 2)

| Column | Type | Description |
|--------|------|-------------|
| customer_key | BIGINT identity | Surrogate PK (one per version) |
| customer_id | VARCHAR(20) | BK |
| first_name, last_name, email | text | Type 1 attributes (overwritten on all versions) |
| city, state, country | text | Type 2 attributes (a change creates a new version) |
| effective_start_date | TIMESTAMP | Version valid from (inclusive). First version: 1900-01-01 |
| effective_end_date | TIMESTAMP | Valid to (exclusive). Current version: 9999-12-31 |
| is_current | BOOLEAN | TRUE for exactly one version per BK (partial unique index) |
| source_updated_at | TIMESTAMP | Source change timestamp that produced/last touched the version |
| run_id | VARCHAR(64) | Run that last changed the row |
| created_at, updated_at | TIMESTAMPTZ | Warehouse row audit timestamps |

Constraints: `UNIQUE (customer_id, effective_start_date)`, partial unique index on
`customer_id WHERE is_current`, gist exclusion constraint preventing overlapping ranges
for the same customer, check that current rows end at 9999-12-31.

### dw.dim_product (SCD Type 2)

Same pattern: `product_key` surrogate, `product_id` BK, Type 2 = `category`, `brand`;
Type 1 = `product_name`, `unit_price` (current list price; the price actually paid is on
the fact).

### dw.dim_date

| Column | Type | Description |
|--------|------|-------------|
| date_key | INTEGER PK | `YYYYMMDD` (check constraint enforces consistency with full_date) |
| full_date | DATE UNIQUE | Calendar date |
| day | SMALLINT | Day of month |
| day_name | VARCHAR(9) | Monday..Sunday |
| week | SMALLINT | ISO-8601 week number |
| month, month_name | SMALLINT, VARCHAR(9) | 1..12, January..December |
| quarter | SMALLINT | 1..4 |
| year | SMALLINT | Calendar year |
| is_weekend | BOOLEAN | Saturday / Sunday |

Generated for full calendar years covering every loaded order date.

### dw.fact_sales

**Grain:** one row per product line within one customer order.

| Column | Type | Kind | Description |
|--------|------|------|-------------|
| sales_key | BIGINT identity | surrogate PK | Warehouse row id |
| source_system | VARCHAR(50) | event key part | Source identity (`ecommerce_oltp`) |
| order_item_id | VARCHAR(30) | event key part | Source line id; `UNIQUE (source_system, order_item_id)` |
| order_id | VARCHAR(30) | degenerate dimension | Groups lines into orders (AOV, order counts) |
| customer_key | BIGINT FK | dimension | Customer version valid at `order_timestamp` |
| product_key | BIGINT FK | dimension | Product version valid at `order_timestamp` |
| date_key | INTEGER FK | dimension | Order date `YYYYMMDD` |
| order_timestamp | TIMESTAMP | context | Event time used for point-in-time resolution |
| order_status | VARCHAR(20) | degenerate attribute | Status of the parent order |
| payment_method | VARCHAR(30) | degenerate attribute | Payment instrument of the parent order |
| quantity | INTEGER | additive measure | Units |
| unit_price | NUMERIC(12,2) | non-additive | Price per unit on the line |
| gross_sales | NUMERIC(14,2) | additive measure | `quantity x unit_price` (check constraint) |
| discount_amount | NUMERIC(12,2) | additive measure | Line discount, `0 <= discount <= gross` |
| net_revenue | NUMERIC(14,2) | additive measure | `gross_sales - discount_amount` (check constraint) |
| record_hash | CHAR(32) | control | md5 of business content - detects corrections |
| source_file | TEXT | lineage | File the line came from |
| run_id | VARCHAR(64) | lineage | Run that inserted / last changed the row |
| loaded_at, updated_at | TIMESTAMPTZ | audit | First load / last change |

## 3. Operational (`ops` schema)

| Table | Key columns |
|-------|-------------|
| pipeline_runs | run_id PK, pipeline_name, start_time, end_time, status (RUNNING/SUCCESS/FAILED/RECONCILIATION_FAILED), source_file (input directory), input/valid/rejected/inserted/updated/failed counts, execution_duration_seconds, error_message |
| pipeline_metrics | metric_id PK, run_id FK, metric_name, metric_value, recorded_at; unique (run_id, metric_name) |
| rejected_records | reject_id PK, run_id FK, source_file, source_row_number, source_record_id, entity_name, rejection_reason (all messages, `; `-separated), reason_codes TEXT[], raw_record JSONB (original values), rejected_at |
| source_files | (run_id, entity_name) PK, file_name, file_path, file_sha256, file_size_bytes, record_count, ingested_at |

`pipeline_runs` counters: `input/valid/rejected` are totals over all four entities;
`inserted/updated` are fact rows; `failed_count` = valid records not published because
the run failed. Per-entity numbers and stage timings are in `pipeline_metrics`
(e.g. `order_items_rejected`, `rejected_order_items_excessive_discount`,
`dim_customer_new_versions`, `facts_rekeyed`, `validate_seconds`, `total_seconds`).

## 4. Rejection reason codes

| Code | Meaning |
|------|---------|
| MISSING_REQUIRED_FIELD | required value empty / blank |
| VALUE_TOO_LONG | exceeds column length |
| INVALID_EMAIL | e-mail format invalid |
| INVALID_TIMESTAMP | not `YYYY-MM-DD HH:MM:SS` or impossible date (e.g. 30 Feb) |
| INVALID_NUMBER | not a plain integer / decimal (e.g. `two`, `1e2`, `1,000`) |
| INVALID_DECIMAL_SCALE | more than 2 decimal places |
| VALUE_OUT_OF_RANGE | too many digits / quantity > 100000 |
| NEGATIVE_VALUE | negative price or discount |
| NON_POSITIVE_QUANTITY | quantity <= 0 |
| INVALID_ALLOWED_VALUE | status / payment method not in allowed set |
| TIMESTAMP_ORDER | updated_at earlier than created_at / order_date |
| EXACT_DUPLICATE | identical copy of an earlier row (first copy kept) |
| CONFLICTING_DUPLICATE | same business key, different content (all copies rejected) |
| DUPLICATE_EMAIL | e-mail used by another customer (batch or warehouse) |
| UNKNOWN_CUSTOMER_REFERENCE / UNKNOWN_ORDER_REFERENCE / UNKNOWN_PRODUCT_REFERENCE | referenced record is neither valid in the batch nor already loaded |
| EXCESSIVE_DISCOUNT | discount_amount > quantity x unit_price |
| MALFORMED_ROW | CSV row has a different number of fields than the header |
