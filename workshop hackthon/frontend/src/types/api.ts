/* API contract shared with the FastAPI backend (backend/app/schemas). */

export type RunStatus = "RUNNING" | "SUCCESS" | "FAILED" | "RECONCILIATION_FAILED";
export type EntityName = "customers" | "products" | "orders" | "order_items";
export type DatasetSource = "uploads" | "generated" | "sample" | "invalid";

export interface Health {
  status: "ok" | "degraded";
  api_version: string;
  time: string;
  database: { connected: boolean; name: string | null; server_version: string | null; error: string | null };
}

export interface AppConfig {
  app_name: string;
  api_version: string;
  environment: string;
  python_version: string;
  database_name: string;
  database_host: string;
  batch_size: number;
  random_seed: number;
  source_system: string;
  datasets_dir: string;
  reports_dir: string;
  cors_origins: string[];
}

export interface PipelineRun {
  run_id: string;
  status: RunStatus;
  source: string | null;
  start_time: string;
  end_time: string | null;
  duration_seconds: number | null;
  input_count: number;
  valid_count: number;
  rejected_count: number;
  inserted_count: number;
  updated_count: number;
  failed_count: number;
  error_message: string | null;
}

export interface StageProgress {
  name: string;
  label: string;
  status: "pending" | "running" | "done" | "failed";
  seconds: number | null;
}

export interface PipelineStatus extends PipelineRun {
  current_stage: string | null;
  stages: StageProgress[];
  progress_percent: number;
  reconciliation_passed: boolean | null;
}

export interface PipelineRunRequest {
  source: DatasetSource;
  strict_schema: boolean;
}

export interface PipelineRunAccepted {
  run_id: string;
  status: RunStatus;
  source: DatasetSource;
}

export interface NamedValue { name: string; value: number }

export interface MonthlySales {
  period: string;
  year: number;
  month: number;
  month_name: string;
  orders: number;
  net_revenue: number;
  mom_growth_pct: number | null;
}

export interface CategorySales {
  category: string;
  orders: number;
  units_sold: number;
  net_revenue: number;
  revenue_share_pct: number;
}

export interface TopProduct {
  product_id: string;
  product_name: string;
  category: string;
  units_sold: number;
  orders: number;
  net_revenue: number;
}

export interface TopCustomer {
  customer_id: string;
  customer_name: string;
  current_city: string;
  orders: number;
  total_spent: number;
}

export interface DashboardSummary {
  total_source_records: number;
  processed_records: number;
  rejected_records: number;
  total_revenue: number;
  total_orders: number;
  pipeline_success_rate: number | null;
  total_runs: number;
  latest_run: PipelineRun | null;
  recent_runs: PipelineRun[];
  revenue_trend: MonthlySales[];
  category_sales: CategorySales[];
  top_products: TopProduct[];
  quality: { valid: number; rejected: number; by_reason: NamedValue[] };
}

export interface ColumnSpec { name: string; required: boolean; type: string }

export interface DatasetFile {
  entity: EntityName;
  file_name: string;
  exists: boolean;
  size_bytes: number | null;
  record_count: number | null;
  modified_at: string | null;
  columns: ColumnSpec[];
  found_columns: string[];
  missing_required_columns: string[];
  unexpected_columns: string[];
  status: "ready" | "missing" | "invalid";
  message: string | null;
}

export interface DatasetCollection { source: DatasetSource; directory: string; files: DatasetFile[] }
export interface DatasetsResponse { sources: DatasetCollection[] }

export interface GenerateRequest {
  customers: number;
  products: number;
  orders: number;
  seed: number;
  include_invalid: boolean;
}

export interface GenerateResponse {
  output_dir: string;
  counts: Record<EntityName, number>;
  invalid_counts: Record<EntityName, number> | null;
  injected_defects: number | null;
  duration_seconds: number;
  generated_at: string;
}

export interface QualitySummary {
  run_id: string | null;
  total_records: number;
  valid_records: number;
  rejected_records: number;
  missing_values: number;
  duplicate_records: number;
  invalid_references: number;
  schema_errors: number;
  business_rule_errors: number;
  by_reason: NamedValue[];
  by_entity: NamedValue[];
}

export interface RejectedRecord {
  reject_id: number;
  run_id: string;
  entity_name: EntityName;
  source_file: string;
  source_row_number: number;
  source_record_id: string | null;
  reason_codes: string[];
  rejection_reason: string;
  raw_record: Record<string, unknown>;
  rejected_at: string;
}

export interface Page<T> { total: number; limit: number; offset: number; items: T[] }

export interface WarehouseColumn {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
  foreign_key: string | null;
}

export interface WarehouseTable {
  schema: string;
  name: string;
  kind: "fact" | "dimension" | "source" | "operational";
  description: string;
  row_count: number;
}

export interface WarehouseTableDetail extends WarehouseTable {
  columns: WarehouseColumn[];
  sample_rows: Record<string, unknown>[];
}

export interface RevenueOverview {
  total_revenue: number;
  gross_sales: number;
  discounts: number;
  orders: number;
  order_lines: number;
  units_sold: number;
  average_order_value: number | null;
  yearly: { year: number; orders: number; units_sold: number; net_revenue: number; yoy_growth_pct: number | null }[];
  order_status: { order_status: string; orders: number; pct_of_orders: number; order_value: number }[];
  cities: { city: string; state: string; customers: number; orders: number; net_revenue: number }[];
  payment_methods: { payment_method: string; orders: number; net_revenue: number; revenue_share_pct: number }[];
}

export interface ReconciliationCheck {
  check_name: string;
  severity: "CRITICAL" | "INFO";
  status: "PASS" | "FAIL" | "INFO";
  source_value: string;
  target_value: string;
  difference: string;
  explanation: string;
}

export interface ReconciliationReport { run_id: string | null; passed: boolean | null; checks: ReconciliationCheck[] }

export interface PerformanceRow {
  order_items: number;
  orders: number;
  generate_s: number;
  extract_s: number;
  validate_s: number;
  transform_s: number;
  load_s: number;
  reconcile_s: number;
  total_s: number;
  rows_per_second: number;
  rerun_s: number;
  rerun_inserted: number;
  status: string;
}

export interface PerformanceReport {
  available: boolean;
  generated_at: string | null;
  postgres: string | null;
  rows: PerformanceRow[];
  experiment: { rows: number; insert_s: number; copy_s: number } | null;
  latest_runs: { run_id: string; order_items: number; seconds: number; rows_per_second: number; stages: Record<string, number> }[];
}

export type ExportReport = "reconciliation" | "quality" | "rejections" | "performance" | "history";
export type ExportFormat = "csv" | "json";
