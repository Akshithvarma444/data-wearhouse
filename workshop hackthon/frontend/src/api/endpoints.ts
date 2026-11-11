import { API_BASE_URL, ApiError, buildUrl, http } from "./client";
import type {
  AppConfig, CategorySales, DashboardSummary, DatasetFile, DatasetsResponse, DatasetSource, EntityName,
  ExportFormat, ExportReport, GenerateRequest, GenerateResponse, Health, MonthlySales, Page, PerformanceReport,
  PipelineRun, PipelineRunAccepted, PipelineRunRequest, PipelineStatus, QualitySummary, ReconciliationReport,
  RejectedRecord, RevenueOverview, TopCustomer, TopProduct, WarehouseTable, WarehouseTableDetail,
} from "../types/api";

async function checkedHealth(signal?: AbortSignal): Promise<Health> {
  const body = await http.get<Health>("/api/health", undefined, signal);
  if (!body || typeof body !== "object" || !body.database || typeof body.database.connected !== "boolean") {
    throw new ApiError(0, `The service at ${API_BASE_URL} answered, but it is not the Sales Warehouse API. Check VITE_API_BASE_URL.`);
  }
  return body;
}

export const api = {
  health: (signal?: AbortSignal) => checkedHealth(signal),
  config: (signal?: AbortSignal) => http.get<AppConfig>("/api/settings/config", undefined, signal),

  dashboardSummary: (signal?: AbortSignal) => http.get<DashboardSummary>("/api/dashboard/summary", undefined, signal),

  datasets: (signal?: AbortSignal) => http.get<DatasetsResponse>("/api/datasets", undefined, signal),
  uploadDataset: (entity: EntityName, file: File) => {
    const form = new FormData();
    form.append("entity", entity);
    form.append("file", file);
    return http.upload<DatasetFile>("/api/datasets/upload", form);
  },

  generate: (body: GenerateRequest) => http.post<GenerateResponse>("/api/generator/generate", body),
  generatorDownloadUrl: (dataset: "generated" | "invalid") => buildUrl("/api/generator/download", { dataset }),

  runPipeline: (body: PipelineRunRequest) => http.post<PipelineRunAccepted>("/api/pipeline/run", body),
  pipelineStatus: (runId: string, signal?: AbortSignal) => http.get<PipelineStatus>(`/api/pipeline/status/${encodeURIComponent(runId)}`, undefined, signal),
  pipelineHistory: (limit = 50, signal?: AbortSignal) => http.get<PipelineRun[]>("/api/pipeline/history", { limit }, signal),

  qualitySummary: (signal?: AbortSignal) => http.get<QualitySummary>("/api/quality/summary", undefined, signal),
  rejections: (params: { entity?: string; reason?: string; run_id?: string; limit: number; offset: number }, signal?: AbortSignal) =>
    http.get<Page<RejectedRecord>>("/api/quality/rejections", params, signal),

  warehouseTables: (signal?: AbortSignal) => http.get<WarehouseTable[]>("/api/warehouse/tables", undefined, signal),
  warehouseTable: (name: string, limit = 10, signal?: AbortSignal) =>
    http.get<WarehouseTableDetail>(`/api/warehouse/tables/${encodeURIComponent(name)}`, { limit }, signal),

  revenue: (signal?: AbortSignal) => http.get<RevenueOverview>("/api/analytics/revenue", undefined, signal),
  monthlySales: (signal?: AbortSignal) => http.get<MonthlySales[]>("/api/analytics/monthly-sales", undefined, signal),
  topProducts: (limit = 10, signal?: AbortSignal) => http.get<TopProduct[]>("/api/analytics/top-products", { limit }, signal),
  topCustomers: (limit = 10, signal?: AbortSignal) => http.get<TopCustomer[]>("/api/analytics/top-customers", { limit }, signal),
  categorySales: (signal?: AbortSignal) => http.get<CategorySales[]>("/api/analytics/category-sales", undefined, signal),

  reconciliation: (runId?: string, signal?: AbortSignal) => http.get<ReconciliationReport>("/api/reports/reconciliation", { run_id: runId }, signal),
  performance: (signal?: AbortSignal) => http.get<PerformanceReport>("/api/reports/performance", undefined, signal),
  exportUrl: (report: ExportReport, format: ExportFormat) => buildUrl("/api/reports/export", { report, format }),
};

export type { DatasetSource };
