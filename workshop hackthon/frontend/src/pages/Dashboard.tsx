import { Link } from "react-router-dom";
import {
  Activity, ArrowRight, BarChart3, CheckCircle2, IndianRupee, Play, RefreshCw, ShieldAlert,
  ShoppingCart, Sparkles, Workflow,
} from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { AsyncContent, EmptyState } from "../components/ui/States";
import { DataTable } from "../components/ui/DataTable";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { HBarChart, TrendChart } from "../components/ui/charts";
import {
  formatCompactMoney, formatDateTime, formatInt, formatMoney, formatPct, formatSeconds, shortRunId,
} from "../utils/format";
import type { DashboardSummary, MonthlySales } from "../types/api";

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/** Year-over-year change of the latest year in the monthly series (real data). */
function yearOverYear(months: MonthlySales[]): { value: number; label: string } | null {
  const byYear = new Map<number, number>();
  months.forEach((m) => byYear.set(m.year, (byYear.get(m.year) ?? 0) + m.net_revenue));
  const years = [...byYear.keys()].sort();
  if (years.length < 2) return null;
  const last = years[years.length - 1], prev = years[years.length - 2];
  const base = byYear.get(prev) ?? 0;
  return base ? { value: (100 * ((byYear.get(last) ?? 0) - base)) / base, label: `${last} vs ${prev}` } : null;
}

function FirstSteps() {
  const steps = [
    { icon: Sparkles, title: "Get some data", text: "Generate a sample dataset, or upload your own CSV files.", to: "/generator", cta: "Generate data", tint: "bg-violet-50 text-violet-600" },
    { icon: Workflow, title: "Run the pipeline", text: "Checks the data, loads the warehouse and reconciles it.", to: "/pipeline", cta: "Run pipeline", tint: "bg-blue-50 text-blue-600" },
    { icon: BarChart3, title: "See the results", text: "Revenue, products, customers and data quality.", to: "/analytics", cta: "Open analytics", tint: "bg-emerald-50 text-emerald-600" },
  ];
  return (
    <Card title="Let's get your warehouse loaded" description="Nothing has been loaded yet. Three quick steps:">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {steps.map((s) => (
          <div key={s.title} className="rounded-lg border border-slate-200 p-4">
            <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${s.tint}`}><s.icon className="h-[18px] w-[18px]" aria-hidden /></span>
            <p className="mt-3 font-medium text-slate-900">{s.title}</p>
            <p className="mt-1 text-sm text-slate-500">{s.text}</p>
            <Link to={s.to} className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-700">{s.cta} <ArrowRight className="h-4 w-4" aria-hidden /></Link>
          </div>
        ))}
      </div>
    </Card>
  );
}

function Content({ s }: { s: DashboardSummary }) {
  const latest = s.latest_run;
  const qualityTotal = s.quality.valid + s.quality.rejected;
  const validPct = qualityTotal ? (100 * s.quality.valid) / qualityTotal : null;

  return (
    <div className="space-y-6">
      {s.total_runs === 0 && <FirstSteps />}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={IndianRupee} tone="brand" label="Net revenue" value={formatCompactMoney(s.total_revenue)} delta={yearOverYear(s.revenue_trend)} />
        <StatCard icon={ShoppingCart} tone="violet" label="Orders" value={formatInt(s.total_orders)} hint="cancelled & returned excluded" />
        <StatCard icon={CheckCircle2} tone="good" label="Valid records" value={formatPct(validPct, 2)} percent={validPct}
          hint={`${formatInt(s.processed_records)} of ${formatInt(s.total_source_records)}`} />
        <StatCard icon={Activity} tone="amber" label="Successful runs" value={formatPct(s.pipeline_success_rate)} hint={`${formatInt(s.total_runs)} runs so far`} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card title="Revenue by month" description="Net revenue, recognised orders" className="xl:col-span-2">
          {s.revenue_trend.length ? <TrendChart data={s.revenue_trend} xKey="period" yKey="net_revenue" height={280} /> : <EmptyState title="No sales yet" />}
        </Card>

        <Card title="Latest run" actions={latest ? <StatusBadge status={latest.status} /> : undefined}>
          {latest ? (
            <div className="space-y-4">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                <div><dt className="text-xs text-slate-500">Started</dt><dd className="text-slate-800">{formatDateTime(latest.start_time)}</dd></div>
                <div><dt className="text-xs text-slate-500">Took</dt><dd className="tabular text-slate-800">{formatSeconds(latest.duration_seconds)}</dd></div>
                <div><dt className="text-xs text-slate-500">Records read</dt><dd className="tabular text-slate-800">{formatInt(latest.input_count)}</dd></div>
                <div><dt className="text-xs text-slate-500">New sales rows</dt><dd className="tabular text-slate-800">{formatInt(latest.inserted_count)}</dd></div>
              </dl>
              <div className="rounded-lg bg-slate-50 p-3">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 text-slate-600"><ShieldAlert className="h-4 w-4 text-rose-500" aria-hidden />Rejected (all runs)</span>
                  <span className="tabular font-semibold text-slate-900">{formatInt(s.rejected_records)}</span>
                </div>
                {s.quality.by_reason.slice(0, 3).map((r) => (
                  <div key={r.name} className="mt-1.5 flex justify-between text-xs text-slate-500">
                    <span className="truncate">{r.name.replaceAll("_", " ").toLowerCase()}</span><span className="tabular">{formatInt(r.value)}</span>
                  </div>
                ))}
              </div>
              <div className="flex gap-3 text-sm">
                <Link to="/quality" className="font-medium text-brand-600 hover:text-brand-700">Data quality</Link>
                <Link to="/history" className="font-medium text-brand-600 hover:text-brand-700">All runs</Link>
              </div>
            </div>
          ) : <p className="text-sm text-slate-500">No runs yet.</p>}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="Revenue by category">
          {s.category_sales.length ? <HBarChart data={s.category_sales} labelKey="category" valueKey="net_revenue" /> : <EmptyState title="No sales yet" />}
        </Card>
        <Card title="Best-selling products" actions={<Link to="/analytics" className="text-sm font-medium text-brand-600 hover:text-brand-700">See all</Link>}>
          {s.top_products.length ? (
            <ul className="divide-y divide-slate-100">
              {s.top_products.map((p, i) => (
                <li key={p.product_id} className="flex items-center gap-3 py-2.5">
                  <span className="tabular w-5 text-sm text-slate-400">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-800">{p.product_name}</p>
                    <p className="text-xs text-slate-500">{p.category}</p>
                  </div>
                  <div className="text-right">
                    <p className="tabular text-sm font-medium text-slate-800">{formatInt(p.units_sold)} sold</p>
                    <p className="tabular text-xs text-slate-500">{formatMoney(p.net_revenue)}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No sales yet" />}
        </Card>
      </div>

      <Card title="Recent runs" actions={<Link to="/history" className="text-sm font-medium text-brand-600 hover:text-brand-700">View history</Link>}>
        <DataTable rows={s.recent_runs} rowKey={(r) => r.run_id} emptyTitle="No runs yet" columns={[
          { key: "run_id", header: "Run", render: (r) => <Link to={`/pipeline?run=${encodeURIComponent(r.run_id)}`} className="font-mono text-xs text-brand-600 hover:underline">{shortRunId(r.run_id)}</Link> },
          { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
          { key: "start_time", header: "Started", render: (r) => formatDateTime(r.start_time) },
          { key: "input_count", header: "Records", align: "right", render: (r) => formatInt(r.input_count) },
          { key: "rejected_count", header: "Rejected", align: "right", render: (r) => formatInt(r.rejected_count) },
          { key: "inserted_count", header: "New rows", align: "right", render: (r) => formatInt(r.inserted_count) },
          { key: "duration_seconds", header: "Took", align: "right", render: (r) => formatSeconds(r.duration_seconds) },
        ]} />
      </Card>
    </div>
  );
}

export default function Dashboard() {
  const state = useApi((signal) => api.dashboardSummary(signal));
  const today = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return (
    <>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">{greeting()} 👋</h1>
          <p className="mt-1 text-sm text-slate-500">{today} · here's how your sales warehouse looks.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={state.reload} loading={state.loading && !!state.data}>Refresh</Button>
          <Link to="/pipeline" className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700">
            <Play className="h-4 w-4" aria-hidden /> Run pipeline
          </Link>
        </div>
      </div>
      <AsyncContent state={state}>{(s) => <Content s={s} />}</AsyncContent>
    </>
  );
}

