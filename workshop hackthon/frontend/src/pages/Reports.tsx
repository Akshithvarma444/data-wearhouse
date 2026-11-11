import { Download } from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { AsyncContent, EmptyState } from "../components/ui/States";
import { DataTable } from "../components/ui/DataTable";
import { StatusBadge } from "../components/ui/StatusBadge";
import { LinkButton } from "../components/ui/Button";
import { HBarChart } from "../components/ui/charts";
import { formatDateTime, formatInt, formatSeconds, shortRunId } from "../utils/format";
import type { ExportReport, ReconciliationCheck } from "../types/api";

function Exports({ report }: { report: ExportReport }) {
  return (
    <div className="flex flex-wrap gap-2">
      <LinkButton href={api.exportUrl(report, "csv")} icon={<Download className="h-4 w-4" />}>CSV</LinkButton>
      <LinkButton href={api.exportUrl(report, "json")} icon={<Download className="h-4 w-4" />}>JSON</LinkButton>
    </div>
  );
}

export default function Reports() {
  const recon = useApi((s) => api.reconciliation(undefined, s));
  const quality = useApi((s) => api.qualitySummary(s));
  const perf = useApi((s) => api.performance(s));

  return (
    <>
      <PageHeader title="Reports" description="Download any report as CSV or JSON." />
      <div className="space-y-6">
        <Card title="Reconciliation report" description="latest completed run" actions={<Exports report="reconciliation" />}>
          <AsyncContent state={recon} isEmpty={(r) => !r.run_id} empty={<EmptyState title="No completed run to reconcile yet" />}>
            {(r) => (
              <>
                <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
                  <span className="text-slate-600">Run <span className="font-mono text-xs">{r.run_id}</span></span>
                  <StatusBadge status={r.passed ? "PASS" : "FAIL"} />
                  <span className="text-slate-500">{r.checks.filter((c) => c.severity === "CRITICAL" && c.status === "PASS").length} / {r.checks.filter((c) => c.severity === "CRITICAL").length} critical checks passed</span>
                </div>
                <DataTable<ReconciliationCheck> rows={r.checks} rowKey={(c) => c.check_name} maxHeight="480px" columns={[
                  { key: "status", header: "Status", render: (c) => <StatusBadge status={c.status} /> },
                  { key: "severity", header: "Severity" },
                  { key: "check_name", header: "Check" },
                  { key: "source_value", header: "Source", align: "right" },
                  { key: "target_value", header: "Target", align: "right" },
                  { key: "explanation", header: "Explanation", wrap: true },
                ]} />
              </>
            )}
          </AsyncContent>
        </Card>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Card title="Data quality report" description="all runs" actions={<Exports report="quality" />}>
            <AsyncContent state={quality}>
              {(q) => (
                <>
                  <p className="mb-3 text-sm text-slate-600">{formatInt(q.rejected_records)} rejected of {formatInt(q.total_records)} input records.</p>
                  {q.by_reason.length ? <HBarChart data={q.by_reason} labelKey="name" valueKey="value" money={false} /> : <EmptyState title="No rejected records" />}
                  <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500">All rejected records: <Exports report="rejections" /></div>
                </>
              )}
            </AsyncContent>
          </Card>

          <Card title="Run history" actions={<Exports report="history" />}>
            <p className="text-sm text-slate-600">All runs from ops.pipeline_runs with counts, timings and errors.</p>
          </Card>
        </div>

        <Card title="Performance report" description="benchmark + recent runs" actions={<Exports report="performance" />}>
          <AsyncContent state={perf}>
            {(p) => (
              <div className="space-y-6">
                {p.available ? (
                  <>
                    <p className="text-sm text-slate-600">Benchmark measured {formatDateTime(p.generated_at)} on PostgreSQL {p.postgres}.
                      {p.experiment && <> COPY loaded {formatInt(p.experiment.rows)} rows in {p.experiment.copy_s}s vs {p.experiment.insert_s}s with row-by-row INSERT ({(p.experiment.insert_s / p.experiment.copy_s).toFixed(0)}× faster).</>}</p>
                    <DataTable rows={p.rows} rowKey={(r) => String(r.order_items)} columns={[
                      { key: "order_items", header: "Order lines", align: "right", render: (r) => formatInt(r.order_items) },
                      { key: "validate_s", header: "Validate", align: "right", render: (r) => formatSeconds(r.validate_s) },
                      { key: "transform_s", header: "Transform", align: "right", render: (r) => formatSeconds(r.transform_s) },
                      { key: "load_s", header: "Load", align: "right", render: (r) => formatSeconds(r.load_s) },
                      { key: "total_s", header: "Total", align: "right", render: (r) => formatSeconds(r.total_s) },
                      { key: "rows_per_second", header: "Rows/s", align: "right", render: (r) => formatInt(Math.round(r.rows_per_second)) },
                      { key: "rerun_inserted", header: "Re-run inserted", align: "right", render: (r) => formatInt(r.rerun_inserted) },
                    ]} />
                  </>
                ) : <p className="text-sm text-slate-500">No benchmark has been run yet. Run <code className="rounded bg-slate-100 px-1">python -m app.cli benchmark</code> in the backend.</p>}
                <div>
                  <h3 className="mb-2 text-sm font-semibold text-slate-800">Recent pipeline runs</h3>
                  {p.latest_runs.length ? (
                    <>
                      <HBarChart data={p.latest_runs.map((r) => ({ ...r, label: shortRunId(r.run_id).slice(0, 18) }))} labelKey="label" valueKey="rows_per_second" money={false} />
                      <p className="mt-1 text-xs text-slate-500">Order lines per second, end to end.</p>
                    </>
                  ) : <EmptyState title="No successful runs yet" />}
                </div>
              </div>
            )}
          </AsyncContent>
        </Card>
      </div>
    </>
  );
}
