"""Request / response schemas of the HTTP API.

These mirror ``frontend/src/types/api.ts`` one to one; FastAPI validates every
response against them, so the contract cannot drift silently.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["RUNNING", "SUCCESS", "FAILED", "RECONCILIATION_FAILED"]
EntityName = Literal["customers", "products", "orders", "order_items"]
DatasetSource = Literal["uploads", "generated", "sample", "invalid"]


# ----------------------------------------------------------------------------- health / config
class DatabaseHealth(BaseModel):
    connected: bool
    name: str | None = None
    server_version: str | None = None
    error: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    api_version: str
    time: datetime
    database: DatabaseHealth


class AppConfig(BaseModel):
    app_name: str
    api_version: str
    environment: str
    python_version: str
    database_name: str
    database_host: str
    batch_size: int
    random_seed: int
    source_system: str
    datasets_dir: str
    reports_dir: str
    cors_origins: list[str]


# ----------------------------------------------------------------------------- pipeline
class PipelineRun(BaseModel):
    run_id: str
    status: RunStatus
    source: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: float | None = None
    input_count: int = 0
    valid_count: int = 0
    rejected_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    failed_count: int = 0
    error_message: str | None = None


class StageProgress(BaseModel):
    name: str
    label: str
    status: Literal["pending", "running", "done", "failed"]
    seconds: float | None = None


class PipelineStatus(PipelineRun):
    current_stage: str | None = None
    stages: list[StageProgress]
    progress_percent: int
    reconciliation_passed: bool | None = None


class PipelineRunRequest(BaseModel):
    source: DatasetSource = "generated"
    strict_schema: bool = False


class PipelineRunAccepted(BaseModel):
    run_id: str
    status: RunStatus
    source: DatasetSource


# ----------------------------------------------------------------------------- dashboard / analytics
class NamedValue(BaseModel):
    name: str
    value: float


class MonthlySales(BaseModel):
    period: str
    year: int
    month: int
    month_name: str
    orders: int
    net_revenue: float
    mom_growth_pct: float | None = None


class CategorySales(BaseModel):
    category: str
    orders: int
    units_sold: int
    net_revenue: float
    revenue_share_pct: float


class TopProduct(BaseModel):
    product_id: str
    product_name: str
    category: str
    units_sold: int
    orders: int
    net_revenue: float


class TopCustomer(BaseModel):
    customer_id: str
    customer_name: str
    current_city: str
    orders: int
    total_spent: float


class QualityOverview(BaseModel):
    valid: int
    rejected: int
    by_reason: list[NamedValue]


class DashboardSummary(BaseModel):
    total_source_records: int
    processed_records: int
    rejected_records: int
    total_revenue: float
    total_orders: int
    pipeline_success_rate: float | None
    total_runs: int
    latest_run: PipelineRun | None
    recent_runs: list[PipelineRun]
    revenue_trend: list[MonthlySales]
    category_sales: list[CategorySales]
    top_products: list[TopProduct]
    quality: QualityOverview


class YearlySales(BaseModel):
    year: int
    orders: int
    units_sold: int
    net_revenue: float
    yoy_growth_pct: float | None = None


class StatusSales(BaseModel):
    order_status: str
    orders: int
    pct_of_orders: float
    order_value: float


class CitySales(BaseModel):
    city: str
    state: str
    customers: int
    orders: int
    net_revenue: float


class PaymentSales(BaseModel):
    payment_method: str
    orders: int
    net_revenue: float
    revenue_share_pct: float


class RevenueOverview(BaseModel):
    total_revenue: float
    gross_sales: float
    discounts: float
    orders: int
    order_lines: int
    units_sold: int
    average_order_value: float | None
    yearly: list[YearlySales]
    order_status: list[StatusSales]
    cities: list[CitySales]
    payment_methods: list[PaymentSales]


# ----------------------------------------------------------------------------- datasets / generator
class ColumnSpec(BaseModel):
    name: str
    required: bool
    type: str


class DatasetFile(BaseModel):
    entity: EntityName
    file_name: str
    exists: bool
    size_bytes: int | None = None
    record_count: int | None = None
    modified_at: datetime | None = None
    columns: list[ColumnSpec]
    found_columns: list[str] = []
    missing_required_columns: list[str] = []
    unexpected_columns: list[str] = []
    status: Literal["ready", "missing", "invalid"]
    message: str | None = None


class DatasetCollection(BaseModel):
    source: DatasetSource
    directory: str
    files: list[DatasetFile]


class DatasetsResponse(BaseModel):
    sources: list[DatasetCollection]


class GenerateRequest(BaseModel):
    customers: int = Field(1000, ge=1, le=500_000)
    products: int = Field(200, ge=1, le=100_000)
    orders: int = Field(10_000, ge=1, le=2_000_000)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    include_invalid: bool = False


class GenerateResponse(BaseModel):
    output_dir: str
    counts: dict[EntityName, int]
    invalid_counts: dict[EntityName, int] | None = None
    injected_defects: int | None = None
    duration_seconds: float
    generated_at: datetime


# ----------------------------------------------------------------------------- quality
class QualitySummary(BaseModel):
    run_id: str | None
    total_records: int
    valid_records: int
    rejected_records: int
    missing_values: int
    duplicate_records: int
    invalid_references: int
    schema_errors: int
    business_rule_errors: int
    by_reason: list[NamedValue]
    by_entity: list[NamedValue]


class RejectedRecord(BaseModel):
    reject_id: int
    run_id: str
    entity_name: EntityName
    source_file: str
    source_row_number: int
    source_record_id: str | None
    reason_codes: list[str]
    rejection_reason: str
    raw_record: dict[str, Any]
    rejected_at: datetime


class RejectionPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RejectedRecord]


# ----------------------------------------------------------------------------- warehouse
class WarehouseColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    foreign_key: str | None = None


class WarehouseTable(BaseModel):
    schema_: str = Field(alias="schema", serialization_alias="schema")
    name: str
    kind: Literal["fact", "dimension", "source", "operational"]
    description: str
    row_count: int

    model_config = {"populate_by_name": True}


class WarehouseTableDetail(WarehouseTable):
    columns: list[WarehouseColumn]
    sample_rows: list[dict[str, Any]]


# ----------------------------------------------------------------------------- reports
class ReconciliationCheck(BaseModel):
    check_name: str
    severity: Literal["CRITICAL", "INFO"]
    status: Literal["PASS", "FAIL", "INFO"]
    source_value: str
    target_value: str
    difference: str
    explanation: str


class ReconciliationReport(BaseModel):
    run_id: str | None
    passed: bool | None
    checks: list[ReconciliationCheck]


class PerformanceRow(BaseModel):
    order_items: int
    orders: int
    generate_s: float
    extract_s: float
    validate_s: float
    transform_s: float
    load_s: float
    reconcile_s: float
    total_s: float
    rows_per_second: float
    rerun_s: float
    rerun_inserted: int
    status: str


class PerformanceExperiment(BaseModel):
    rows: int
    insert_s: float
    copy_s: float


class RunPerformance(BaseModel):
    run_id: str
    order_items: int
    seconds: float
    rows_per_second: float
    stages: dict[str, float]


class PerformanceReport(BaseModel):
    available: bool
    generated_at: str | None
    postgres: str | None
    rows: list[PerformanceRow]
    experiment: PerformanceExperiment | None
    latest_runs: list[RunPerformance]
