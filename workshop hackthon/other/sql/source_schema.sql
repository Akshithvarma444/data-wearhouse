-- =============================================================================
-- source_schema.sql
-- Raw landing layer + typed transactional source replica (ODS).
--
--   raw.*     Every input record exactly as read from the CSV (all TEXT), one
--             row per record per pipeline run. Never updated. Used for
--             traceability, replay and source-side reconciliation.
--   source.*  The four transactional entities with real types, primary keys,
--             foreign keys, unique and check constraints. Holds the latest
--             valid state of every business key (upserted by the pipeline).
--
-- The script is idempotent (IF NOT EXISTS everywhere) and safe to re-run.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS source;

-- -----------------------------------------------------------------------------
-- RAW LANDING TABLES
-- validation_status: VALID    -> passed all rules and was published
--                    REJECTED -> written to ops.rejected_records
--                    SKIPPED  -> valid, but outside a requested backfill window
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.customers (
    run_id              VARCHAR(64)  NOT NULL,
    source_file         TEXT         NOT NULL,
    source_row_number   INTEGER      NOT NULL,
    customer_id         TEXT,
    first_name          TEXT,
    last_name           TEXT,
    email               TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    extra_attributes    JSONB,
    validation_status   VARCHAR(10)  NOT NULL,
    ingested_at         TIMESTAMPTZ  NOT NULL,
    CONSTRAINT pk_raw_customers PRIMARY KEY (run_id, source_row_number),
    CONSTRAINT ck_raw_customers_status CHECK (validation_status IN ('VALID', 'REJECTED', 'SKIPPED'))
);

CREATE TABLE IF NOT EXISTS raw.products (
    run_id              VARCHAR(64)  NOT NULL,
    source_file         TEXT         NOT NULL,
    source_row_number   INTEGER      NOT NULL,
    product_id          TEXT,
    product_name        TEXT,
    category            TEXT,
    brand               TEXT,
    unit_price          TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    extra_attributes    JSONB,
    validation_status   VARCHAR(10)  NOT NULL,
    ingested_at         TIMESTAMPTZ  NOT NULL,
    CONSTRAINT pk_raw_products PRIMARY KEY (run_id, source_row_number),
    CONSTRAINT ck_raw_products_status CHECK (validation_status IN ('VALID', 'REJECTED', 'SKIPPED'))
);

CREATE TABLE IF NOT EXISTS raw.orders (
    run_id              VARCHAR(64)  NOT NULL,
    source_file         TEXT         NOT NULL,
    source_row_number   INTEGER      NOT NULL,
    order_id            TEXT,
    customer_id         TEXT,
    order_date          TEXT,
    order_status        TEXT,
    payment_method      TEXT,
    updated_at          TEXT,
    extra_attributes    JSONB,
    validation_status   VARCHAR(10)  NOT NULL,
    ingested_at         TIMESTAMPTZ  NOT NULL,
    CONSTRAINT pk_raw_orders PRIMARY KEY (run_id, source_row_number),
    CONSTRAINT ck_raw_orders_status CHECK (validation_status IN ('VALID', 'REJECTED', 'SKIPPED'))
);

CREATE TABLE IF NOT EXISTS raw.order_items (
    run_id              VARCHAR(64)  NOT NULL,
    source_file         TEXT         NOT NULL,
    source_row_number   INTEGER      NOT NULL,
    order_item_id       TEXT,
    order_id            TEXT,
    product_id          TEXT,
    quantity            TEXT,
    unit_price          TEXT,
    discount_amount     TEXT,
    created_at          TEXT,
    extra_attributes    JSONB,
    validation_status   VARCHAR(10)  NOT NULL,
    ingested_at         TIMESTAMPTZ  NOT NULL,
    CONSTRAINT pk_raw_order_items PRIMARY KEY (run_id, source_row_number),
    CONSTRAINT ck_raw_order_items_status CHECK (validation_status IN ('VALID', 'REJECTED', 'SKIPPED'))
);

-- -----------------------------------------------------------------------------
-- TYPED SOURCE (ODS) TABLES
-- Money is NUMERIC(12,2) - never floating point.
-- last_run_id / last_source_file / loaded_at are lineage columns.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source.customers (
    customer_id         VARCHAR(20)   NOT NULL,
    first_name          VARCHAR(100)  NOT NULL,
    last_name           VARCHAR(100),
    email               VARCHAR(255)  NOT NULL,
    city                VARCHAR(100)  NOT NULL,
    state               VARCHAR(100)  NOT NULL,
    country             VARCHAR(100)  NOT NULL,
    created_at          TIMESTAMP     NOT NULL,
    updated_at          TIMESTAMP     NOT NULL,
    last_run_id         VARCHAR(64)   NOT NULL,
    last_source_file    TEXT          NOT NULL,
    loaded_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_source_customers PRIMARY KEY (customer_id),
    CONSTRAINT uq_source_customers_email UNIQUE (email),
    CONSTRAINT ck_source_customers_ts CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS source.products (
    product_id          VARCHAR(20)   NOT NULL,
    product_name        VARCHAR(200)  NOT NULL,
    category            VARCHAR(100)  NOT NULL,
    brand               VARCHAR(100)  NOT NULL,
    unit_price          NUMERIC(12,2) NOT NULL,
    created_at          TIMESTAMP     NOT NULL,
    updated_at          TIMESTAMP     NOT NULL,
    last_run_id         VARCHAR(64)   NOT NULL,
    last_source_file    TEXT          NOT NULL,
    loaded_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_source_products PRIMARY KEY (product_id),
    CONSTRAINT ck_source_products_price CHECK (unit_price >= 0),
    CONSTRAINT ck_source_products_ts CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS source.orders (
    order_id            VARCHAR(30)   NOT NULL,
    customer_id         VARCHAR(20)   NOT NULL,
    order_date          TIMESTAMP     NOT NULL,
    order_status        VARCHAR(20)   NOT NULL,
    payment_method      VARCHAR(30)   NOT NULL,
    updated_at          TIMESTAMP     NOT NULL,
    last_run_id         VARCHAR(64)   NOT NULL,
    last_source_file    TEXT          NOT NULL,
    loaded_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_source_orders PRIMARY KEY (order_id),
    CONSTRAINT fk_source_orders_customer FOREIGN KEY (customer_id)
        REFERENCES source.customers (customer_id),
    CONSTRAINT ck_source_orders_status CHECK (
        order_status IN ('PLACED', 'SHIPPED', 'DELIVERED', 'CANCELLED', 'RETURNED')),
    CONSTRAINT ck_source_orders_payment CHECK (
        payment_method IN ('UPI', 'CREDIT_CARD', 'DEBIT_CARD', 'NET_BANKING',
                           'CASH_ON_DELIVERY', 'WALLET', 'UNKNOWN')),
    CONSTRAINT ck_source_orders_ts CHECK (updated_at >= order_date)
);

CREATE TABLE IF NOT EXISTS source.order_items (
    order_item_id       VARCHAR(30)   NOT NULL,
    order_id            VARCHAR(30)   NOT NULL,
    product_id          VARCHAR(20)   NOT NULL,
    quantity            INTEGER       NOT NULL,
    unit_price          NUMERIC(12,2) NOT NULL,
    discount_amount     NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at          TIMESTAMP     NOT NULL,
    last_run_id         VARCHAR(64)   NOT NULL,
    last_source_file    TEXT          NOT NULL,
    loaded_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_source_order_items PRIMARY KEY (order_item_id),
    CONSTRAINT fk_source_order_items_order FOREIGN KEY (order_id)
        REFERENCES source.orders (order_id),
    CONSTRAINT fk_source_order_items_product FOREIGN KEY (product_id)
        REFERENCES source.products (product_id),
    CONSTRAINT ck_source_order_items_qty CHECK (quantity > 0),
    CONSTRAINT ck_source_order_items_price CHECK (unit_price >= 0),
    CONSTRAINT ck_source_order_items_discount CHECK (
        discount_amount >= 0 AND discount_amount <= quantity * unit_price)
);
