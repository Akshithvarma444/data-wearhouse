# Test Scenarios

Run from `backend/`: `pytest -v` (120 tests: 109 engine + 11 API). Database tests use an isolated database
(`<DB_NAME>_test`, or `TEST_DATABASE_URL`); the suite refuses to run against a database
whose name does not end in `_test`, and truncates tables before each database test.

## Requirement -> test mapping

### Ingestion (`backend/tests/test_ingestion.py`)
| Required case | Test |
|---|---|
| Valid CSV file | `test_valid_csv_is_extracted_completely` |
| Missing CSV file | `test_missing_csv_file_raises`, `test_missing_source_directory_raises` |
| Empty CSV file | `test_empty_csv_file_raises`, `test_whitespace_only_file_raises`, `test_header_only_file_is_a_valid_empty_batch` |
| Missing required column | `test_missing_required_column_raises` (+ `test_missing_optional_column_is_added_empty`) |
| Unexpected column | `test_unexpected_column_is_reported_and_preserved`, `test_unexpected_column_fails_in_strict_mode` |
| Extra | wrong extension, duplicate header names, header case/whitespace, malformed row captured, source dir from env |

### Validation (`backend/tests/test_validation.py`)
| Required case | Test |
|---|---|
| Valid records | `test_valid_records_pass`, `test_sample_dataset_is_fully_valid` |
| Missing customer ID | `test_missing_customer_id` |
| Invalid email | `test_invalid_email` |
| Duplicate customer ID | `test_duplicate_customer_id_exact_keeps_first`, `test_duplicate_customer_id_conflicting_rejects_all_and_cascades` |
| Duplicate order item ID | `test_duplicate_order_item_id` |
| Negative quantity | `test_negative_quantity`, `test_zero_and_non_numeric_quantity` |
| Negative price | `test_negative_price` |
| Excessive discount | `test_excessive_discount` (+ edge `test_discount_equal_to_gross_is_allowed`) |
| Invalid product reference | `test_invalid_product_reference` |
| Invalid customer reference | `test_invalid_customer_reference` |
| Malformed date | `test_malformed_dates` |
| Extra | multiple reasons per record, decimal scale, timestamp order, duplicate e-mail, raw values preserved, references satisfied by earlier loads, money formats (`1e2`, `NaN`, `1,000.00`, `₹100`), every injected defect in `other/datasets/invalid` detected |

### Transformation (`backend/tests/test_transformation.py`)
| Required case | Test |
|---|---|
| Correct gross sales / net revenue | `test_gross_sales_and_net_revenue_are_exact`, `test_float_arithmetic_would_be_wrong_but_measures_are_not` |
| Correct date key | `test_date_key_generation`, `test_date_dimension_attributes` |
| Correct surrogate key mapping | `test_surrogate_key_mapping`, `test_unresolvable_keys_are_reported` |
| Correct historical dimension resolution | `test_historical_dimension_resolution_uses_event_date` (incl. exact boundary instant) |
| Extra | customer/product cleaning + defaults, full `transform_sales`, discount guard |

### Loading (`backend/tests/test_loading.py`)
| Required case | Test |
|---|---|
| Successful dimension loading | `test_dimension_load_creates_current_members`, `test_scd2_change_expires_old_and_creates_new_version`, `test_product_scd2_on_category_change`, `test_date_dimension_load_is_idempotent` |
| Successful fact loading | `test_successful_fact_load` |
| Foreign key enforcement | `test_foreign_keys_are_enforced` |
| Duplicate fact prevention | `test_duplicate_fact_prevention` |
| Safe rerun | `test_dimension_rerun_creates_no_duplicates` |
| Transaction rollback | `test_transaction_rollback_leaves_no_partial_data` |
| Extra | Type 1 overwrite, stale update ignored, corrected fact in place, measure check constraints, one-current-version index, no-overlap exclusion constraint |

### Idempotency (`backend/tests/test_idempotency.py`)
`test_same_input_twice_changes_nothing`, `test_sample_dataset_three_runs_keep_totals`,
`test_corrected_source_record_updates_fact_in_place`,
`test_order_status_change_updates_every_line`, `test_stale_order_update_is_ignored`,
`test_run_id_cannot_be_reused`.

### Pipeline (`backend/tests/test_pipeline.py`)
| Required case | Test |
|---|---|
| Complete successful pipeline | `test_complete_successful_pipeline` |
| Empty batch | `test_empty_batch` |
| Partial invalid batch | `test_partial_invalid_batch` |
| Database failure | `test_database_failure_rolls_back_warehouse` |
| Retry after failure | `test_retry_after_failure` |
| Late-arriving records | `test_late_arriving_order` |
| Unexpected schema change | `test_unexpected_schema_change_is_tolerated_and_recorded`, `test_unexpected_schema_change_fails_in_strict_mode`, `test_missing_required_column_fails_run`, `test_missing_file_fails_run` |
| Scenario 1 (Hyderabad -> Chennai) | `test_scd2_customer_move` |
| Scenario 3 (missing product) | `test_missing_product_reference_is_quarantined` |
| Extra | `test_late_dimension_change_rekeys_existing_facts`, `test_backfill_window` |

### Reconciliation (`backend/tests/test_reconciliation.py`)
Report written and passing; input = valid + rejected; a deleted fact row is detected; a
fact pointing at the wrong SCD version is detected; a pipeline whose load drops a row is
marked `RECONCILIATION_FAILED` instead of SUCCESS; unknown run id.

### Analytics (`backend/tests/test_analytics.py`)
Expected results for the sample dataset are computed **independently** from the CSV files
with Python `Decimal` (`backend/tests/fixtures/build_expected_results.py` ->
`backend/tests/fixtures/expected_sample_results.json`) and compared with the SQL results: total
revenue, yearly/monthly revenue, category/city/payment revenue, top-10 products, status
analysis, AOV computed at order level (and shown to differ from a line-level average),
and a check that joining all dimensions does not double count.

### Generator (`backend/tests/test_generator.py`)
Byte-identical output for the same seed, different output for a different seed, exact
order-item count, referential integrity, money format, discount <= gross, clean data
passes validation, invalid data kept separate with a manifest.

## Domain-specific edge cases covered
1. **Late-arriving dimension change** re-keys an already-loaded fact
   (`test_late_dimension_change_rekeys_existing_facts`).
2. **Exact SCD boundary**: a sale at exactly the change instant belongs to the new version
   (`test_historical_dimension_resolution_uses_event_date`).
3. **100 % discount** is legal, 1 paisa more is rejected
   (`test_discount_equal_to_gross_is_allowed`, `test_excessive_discount`).
4. **Floating-point drift** never reaches revenue
   (`test_float_arithmetic_would_be_wrong_but_measures_are_not`).
5. **Order status change after load** (RETURNED) propagates to every line of the order
   (`test_order_status_change_updates_every_line`).
6. **Out-of-order updates** (older `updated_at`) do not overwrite newer state
   (`test_stale_order_update_is_ignored`, `test_out_of_order_dimension_update_is_ignored`).
7. **Conflicting duplicate customer** cascades to its orders and lines
   (`test_duplicate_customer_id_conflicting_rejects_all_and_cascades`).

### HTTP API (`backend/tests/test_api.py`)
Every endpoint used by the frontend, through FastAPI's TestClient against the isolated
test database: health/config (no secrets), pipeline run + status polling to completion and
an idempotent re-run (0 inserted), unknown run (404), incomplete dataset (422), unknown
source (422), dashboard / analytics / reports equal to the independently computed expected
results, quality summary and rejection filtering after the invalid run (including an
injection attempt in the `reason` filter -> 422), warehouse catalog with PK/FK metadata and
a non-whitelisted table (404), data generation + zip download + input validation, uploads
(valid, wrong extension, missing column, empty file, path-like entity) and a full pipeline
run from uploaded files.

### Frontend
`npm run build` type-checks every page with `tsc` in strict mode and builds the production
bundle. The pages were checked in a browser against the running API (screenshots in
`other/screenshots/`).
8. **Stale order header with re-delivered lines** - an older copy of an order arrives together with its lines; the source keeps the newer header and the facts must follow it, not the stale batch copy (`test_stale_order_header_with_redelivered_lines`). Found by the reconciliation check during manual testing.
