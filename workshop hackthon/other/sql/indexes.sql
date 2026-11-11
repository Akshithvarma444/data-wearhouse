-- =============================================================================
-- indexes.sql
-- Secondary indexes. Primary keys / unique constraints already create their
-- own indexes and are declared in the schema files. Idempotent.
-- =============================================================================

-- ---- source (ODS): index every foreign key column -------------------------
CREATE INDEX IF NOT EXISTS ix_source_orders_customer_id     ON source.orders (customer_id);
CREATE INDEX IF NOT EXISTS ix_source_orders_order_date      ON source.orders (order_date);
CREATE INDEX IF NOT EXISTS ix_source_order_items_order_id   ON source.order_items (order_id);
CREATE INDEX IF NOT EXISTS ix_source_order_items_product_id ON source.order_items (product_id);

-- ---- dimensions --------------------------------------------------------------
-- Only one current version per business key (SCD2 guard).
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_customer_current
    ON dw.dim_customer (customer_id) WHERE is_current;
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_product_current
    ON dw.dim_product (product_id) WHERE is_current;

-- Point-in-time lookups by business key + effective date.
CREATE INDEX IF NOT EXISTS ix_dim_customer_bk_range
    ON dw.dim_customer (customer_id, effective_start_date, effective_end_date);
CREATE INDEX IF NOT EXISTS ix_dim_product_bk_range
    ON dw.dim_product (product_id, effective_start_date, effective_end_date);

-- Common slicing attributes.
CREATE INDEX IF NOT EXISTS ix_dim_customer_city    ON dw.dim_customer (city);
CREATE INDEX IF NOT EXISTS ix_dim_product_category ON dw.dim_product (category);
CREATE INDEX IF NOT EXISTS ix_dim_date_year_month  ON dw.dim_date (year, month);

-- ---- fact: index every foreign key + degenerate dimension ------------------
CREATE INDEX IF NOT EXISTS ix_fact_sales_customer_key ON dw.fact_sales (customer_key);
CREATE INDEX IF NOT EXISTS ix_fact_sales_product_key  ON dw.fact_sales (product_key);
CREATE INDEX IF NOT EXISTS ix_fact_sales_date_key     ON dw.fact_sales (date_key);
CREATE INDEX IF NOT EXISTS ix_fact_sales_order_id     ON dw.fact_sales (order_id);
CREATE INDEX IF NOT EXISTS ix_fact_sales_run_id       ON dw.fact_sales (run_id);

-- ---- operations --------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_start      ON ops.pipeline_runs (start_time DESC);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_status     ON ops.pipeline_runs (status);
CREATE INDEX IF NOT EXISTS ix_rejected_records_run     ON ops.rejected_records (run_id, entity_name);
CREATE INDEX IF NOT EXISTS ix_rejected_records_reasons ON ops.rejected_records USING gin (reason_codes);
CREATE INDEX IF NOT EXISTS ix_raw_order_items_item_id  ON raw.order_items (order_item_id);
