-- =============================================================================
-- warehouse_schema.sql
-- Star schema (dw) + operational metadata (ops).
--
--   dw.dim_customer   SCD Type 2 (city/state/country), surrogate key
--   dw.dim_product    SCD Type 2 (category/brand),      surrogate key
--   dw.dim_date       one row per calendar day, key = YYYYMMDD
--   dw.fact_sales     grain = ONE ROW PER ORDER LINE (order_item_id)
--
--   ops.pipeline_runs     one row per pipeline execution
--   ops.pipeline_metrics  name/value metrics per run
--   ops.rejected_records  quarantine (one row per rejected source record)
--   ops.source_files      file registry (checksum, size, row count)
--
-- Idempotent: safe to execute repeatedly.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- needed for SCD2 no-overlap constraints

CREATE SCHEMA IF NOT EXISTS dw;
CREATE SCHEMA IF NOT EXISTS ops;

-- -----------------------------------------------------------------------------
-- OPERATIONAL TABLES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id                       VARCHAR(64)   NOT NULL,
    pipeline_name                VARCHAR(100)  NOT NULL,
    start_time                   TIMESTAMPTZ   NOT NULL,
    end_time                     TIMESTAMPTZ,
    status                       VARCHAR(30)   NOT NULL,
    source_file                  TEXT,
    input_count                  BIGINT        NOT NULL DEFAULT 0,
    valid_count                  BIGINT        NOT NULL DEFAULT 0,
    rejected_count               BIGINT        NOT NULL DEFAULT 0,
    inserted_count               BIGINT        NOT NULL DEFAULT 0,
    updated_count                BIGINT        NOT NULL DEFAULT 0,
    failed_count                 BIGINT        NOT NULL DEFAULT 0,
    execution_duration_seconds   NUMERIC(12,3),
    error_message                TEXT,
    CONSTRAINT pk_pipeline_runs PRIMARY KEY (run_id),
    CONSTRAINT ck_pipeline_runs_status CHECK (
        status IN ('RUNNING', 'SUCCESS', 'FAILED', 'RECONCILIATION_FAILED'))
);

CREATE TABLE IF NOT EXISTS ops.pipeline_metrics (
    metric_id      BIGINT GENERATED ALWAYS AS IDENTITY,
    run_id         VARCHAR(64)    NOT NULL,
    metric_name    VARCHAR(100)   NOT NULL,
    metric_value   NUMERIC(20,4)  NOT NULL,
    recorded_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT pk_pipeline_metrics PRIMARY KEY (metric_id),
    CONSTRAINT fk_pipeline_metrics_run FOREIGN KEY (run_id)
        REFERENCES ops.pipeline_runs (run_id),
    CONSTRAINT uq_pipeline_metrics_run_name UNIQUE (run_id, metric_name)
);

CREATE TABLE IF NOT EXISTS ops.rejected_records (
    reject_id           BIGINT GENERATED ALWAYS AS IDENTITY,
    run_id              VARCHAR(64)   NOT NULL,
    source_file         TEXT          NOT NULL,
    source_row_number   INTEGER       NOT NULL,
    source_record_id    TEXT,
    entity_name         VARCHAR(30)   NOT NULL,
    rejection_reason    TEXT          NOT NULL,
    reason_codes        TEXT[]        NOT NULL,
    raw_record          JSONB         NOT NULL,
    rejected_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_rejected_records PRIMARY KEY (reject_id),
    CONSTRAINT fk_rejected_records_run FOREIGN KEY (run_id)
        REFERENCES ops.pipeline_runs (run_id),
    CONSTRAINT uq_rejected_records_row UNIQUE (run_id, entity_name, source_row_number),
    CONSTRAINT ck_rejected_records_entity CHECK (
        entity_name IN ('customers', 'products', 'orders', 'order_items'))
);

CREATE TABLE IF NOT EXISTS ops.source_files (
    run_id            VARCHAR(64)  NOT NULL,
    entity_name       VARCHAR(30)  NOT NULL,
    file_name         TEXT         NOT NULL,
    file_path         TEXT         NOT NULL,
    file_sha256       CHAR(64)     NOT NULL,
    file_size_bytes   BIGINT       NOT NULL,
    record_count      BIGINT       NOT NULL,
    ingested_at       TIMESTAMPTZ  NOT NULL,
    CONSTRAINT pk_source_files PRIMARY KEY (run_id, entity_name),
    CONSTRAINT fk_source_files_run FOREIGN KEY (run_id)
        REFERENCES ops.pipeline_runs (run_id)
);

-- -----------------------------------------------------------------------------
-- DIMENSIONS
-- effective ranges are half-open: [effective_start_date, effective_end_date)
-- First version of a key starts at 1900-01-01, current version ends 9999-12-31.
-- created_at / updated_at are warehouse row-audit timestamps;
-- source_updated_at is the source system's change timestamp that drove SCD2.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_key           BIGINT GENERATED ALWAYS AS IDENTITY,
    customer_id            VARCHAR(20)   NOT NULL,
    first_name             VARCHAR(100)  NOT NULL,
    last_name              VARCHAR(100),
    email                  VARCHAR(255)  NOT NULL,
    city                   VARCHAR(100)  NOT NULL,
    state                  VARCHAR(100)  NOT NULL,
    country                VARCHAR(100)  NOT NULL,
    effective_start_date   TIMESTAMP     NOT NULL,
    effective_end_date     TIMESTAMP     NOT NULL DEFAULT '9999-12-31 00:00:00',
    is_current             BOOLEAN       NOT NULL,
    source_updated_at      TIMESTAMP     NOT NULL,
    run_id                 VARCHAR(64)   NOT NULL,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_dim_customer PRIMARY KEY (customer_key),
    CONSTRAINT uq_dim_customer_version UNIQUE (customer_id, effective_start_date),
    CONSTRAINT ck_dim_customer_range CHECK (effective_end_date > effective_start_date),
    CONSTRAINT ck_dim_customer_current CHECK (
        (is_current AND effective_end_date = '9999-12-31 00:00:00')
        OR (NOT is_current AND effective_end_date < '9999-12-31 00:00:00')),
    CONSTRAINT ex_dim_customer_no_overlap EXCLUDE USING gist (
        customer_id WITH =,
        tsrange(effective_start_date, effective_end_date, '[)') WITH &&)
);

CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_key            BIGINT GENERATED ALWAYS AS IDENTITY,
    product_id             VARCHAR(20)   NOT NULL,
    product_name           VARCHAR(200)  NOT NULL,
    category               VARCHAR(100)  NOT NULL,
    brand                  VARCHAR(100)  NOT NULL,
    unit_price             NUMERIC(12,2) NOT NULL,
    effective_start_date   TIMESTAMP     NOT NULL,
    effective_end_date     TIMESTAMP     NOT NULL DEFAULT '9999-12-31 00:00:00',
    is_current             BOOLEAN       NOT NULL,
    source_updated_at      TIMESTAMP     NOT NULL,
    run_id                 VARCHAR(64)   NOT NULL,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_dim_product PRIMARY KEY (product_key),
    CONSTRAINT uq_dim_product_version UNIQUE (product_id, effective_start_date),
    CONSTRAINT ck_dim_product_price CHECK (unit_price >= 0),
    CONSTRAINT ck_dim_product_range CHECK (effective_end_date > effective_start_date),
    CONSTRAINT ck_dim_product_current CHECK (
        (is_current AND effective_end_date = '9999-12-31 00:00:00')
        OR (NOT is_current AND effective_end_date < '9999-12-31 00:00:00')),
    CONSTRAINT ex_dim_product_no_overlap EXCLUDE USING gist (
        product_id WITH =,
        tsrange(effective_start_date, effective_end_date, '[)') WITH &&)
);

CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_key     INTEGER      NOT NULL,
    full_date    DATE         NOT NULL,
    day          SMALLINT     NOT NULL,
    day_name     VARCHAR(9)   NOT NULL,
    week         SMALLINT     NOT NULL,
    month        SMALLINT     NOT NULL,
    month_name   VARCHAR(9)   NOT NULL,
    quarter      SMALLINT     NOT NULL,
    year         SMALLINT     NOT NULL,
    is_weekend   BOOLEAN      NOT NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY (date_key),
    CONSTRAINT uq_dim_date_full_date UNIQUE (full_date),
    CONSTRAINT ck_dim_date_key CHECK (
        date_key = (EXTRACT(YEAR FROM full_date) * 10000
                  + EXTRACT(MONTH FROM full_date) * 100
                  + EXTRACT(DAY FROM full_date))::INTEGER)
);

-- -----------------------------------------------------------------------------
-- FACT
-- Grain: one row per product line within one customer order.
-- Event key: (source_system, order_item_id) - UNIQUE, drives idempotent upserts.
-- record_hash: md5 of all business content; a re-delivered identical event is a
-- no-op, a corrected event updates the row in place.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.fact_sales (
    sales_key          BIGINT GENERATED ALWAYS AS IDENTITY,
    source_system      VARCHAR(50)   NOT NULL,
    order_item_id      VARCHAR(30)   NOT NULL,
    order_id           VARCHAR(30)   NOT NULL,
    customer_key       BIGINT        NOT NULL,
    product_key        BIGINT        NOT NULL,
    date_key           INTEGER       NOT NULL,
    order_timestamp    TIMESTAMP     NOT NULL,
    order_status       VARCHAR(20)   NOT NULL,
    payment_method     VARCHAR(30)   NOT NULL,
    quantity           INTEGER       NOT NULL,
    unit_price         NUMERIC(12,2) NOT NULL,
    gross_sales        NUMERIC(14,2) NOT NULL,
    discount_amount    NUMERIC(12,2) NOT NULL,
    net_revenue        NUMERIC(14,2) NOT NULL,
    record_hash        CHAR(32)      NOT NULL,
    source_file        TEXT          NOT NULL,
    run_id             VARCHAR(64)   NOT NULL,
    loaded_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ,
    CONSTRAINT pk_fact_sales PRIMARY KEY (sales_key),
    CONSTRAINT uq_fact_sales_event UNIQUE (source_system, order_item_id),
    CONSTRAINT fk_fact_sales_customer FOREIGN KEY (customer_key)
        REFERENCES dw.dim_customer (customer_key),
    CONSTRAINT fk_fact_sales_product FOREIGN KEY (product_key)
        REFERENCES dw.dim_product (product_key),
    CONSTRAINT fk_fact_sales_date FOREIGN KEY (date_key)
        REFERENCES dw.dim_date (date_key),
    CONSTRAINT ck_fact_sales_qty CHECK (quantity > 0),
    CONSTRAINT ck_fact_sales_price CHECK (unit_price >= 0),
    CONSTRAINT ck_fact_sales_gross CHECK (gross_sales = quantity * unit_price),
    CONSTRAINT ck_fact_sales_discount CHECK (discount_amount >= 0 AND discount_amount <= gross_sales),
    CONSTRAINT ck_fact_sales_net CHECK (net_revenue = gross_sales - discount_amount)
);

-- Deterministic content hash used by the fact upsert and by fact re-keying.
CREATE OR REPLACE FUNCTION dw.sales_record_hash(
    p_customer_key     BIGINT,
    p_product_key      BIGINT,
    p_date_key         INTEGER,
    p_order_id         VARCHAR,
    p_order_timestamp  TIMESTAMP,
    p_order_status     VARCHAR,
    p_payment_method   VARCHAR,
    p_quantity         INTEGER,
    p_unit_price       NUMERIC,
    p_discount_amount  NUMERIC
) RETURNS CHAR(32)
LANGUAGE sql STABLE
AS $$
    SELECT md5(concat_ws('|',
        p_customer_key, p_product_key, p_date_key, p_order_id,
        to_char(p_order_timestamp, 'YYYY-MM-DD HH24:MI:SS'),
        p_order_status, p_payment_method, p_quantity,
        round(p_unit_price, 2)::text, round(p_discount_amount, 2)::text))::CHAR(32)
$$;
