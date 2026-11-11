import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Check, CheckCircle2, Circle, Database, FileSearch, Loader2, Play, Scale, ShieldCheck, Shuffle,
  Upload, XCircle, type LucideIcon,
} from "lucide-react";
import { api } from "../api/endpoints";
import { usePolling } from "../hooks/usePolling";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { SelectField } from "../components/ui/Field";
import { ErrorState } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useToast } from "../components/ui/Toast";
import { formatDateTime, formatInt, formatSeconds } from "../utils/format";
import type { DatasetSource, PipelineStatus, StageProgress } from "../types/api";

type PhaseState = "pending" | "running" | "done" | "failed";

/** The five pipeline phases and the backend stages that belong to each. */
const PHASES: { name: string; icon: LucideIcon; stages: string[]; text: string }[] = [
  { name: "Extract", icon: FileSearch, stages: ["extract"], text: "read CSV files" },
  { name: "Validate", icon: ShieldCheck, stages: ["validate", "land_raw_and_quarantine"], text: "rules + quarantine" },
  { name: "Transform", icon: Shuffle, stages: ["transform", "transform_facts", "resolve_keys"], text: "facts + keys" },
  { name: "Load", icon: Database, stages: ["load_source", "load_dimensions", "load_dim_date", "load_facts"], text: "dims (SCD2) + facts" },
  { name: "Reconcile", icon: Scale, stages: ["reconcile"], text: "source vs target" },
];

const SOURCES: { value: DatasetSource; label: string; hint: string }[] = [
  { value: "generated", label: "Generated dataset", hint: "Created on the Data Generator page" },
  { value: "uploads", label: "Uploaded files", hint: "CSV files uploaded on Data Sources" },
  { value: "sample", label: "Sample dataset", hint: "100 order lines, always available" },
  { value: "invalid", label: "Invalid test dataset", hint: "Shows quarantine and rejection reasons" },
];
const POLL_MS = 1000;

function phaseState(stages: StageProgress[] | undefined, names: string[]): PhaseState {
  if (!stages) return "pending";
  const own = stages.filter((s) => names.includes(s.name));
  if (own.some((s) => s.status === "failed")) return "failed";
  if (own.every((s) => s.status === "done")) return "done";
  if (own.some((s) => s.status === "running" || s.status === "done")) return "running";
  return "pending";
}

function Stepper({ status }: { status: PipelineStatus | null }) {
  return (
    <ol className="grid grid-cols-1 gap-3 sm:grid-cols-5">
      {PHASES.map((phase, i) => {
        const state = phaseState(status?.stages, phase.stages);
        const seconds = status?.stages.filter((s) => phase.stages.includes(s.name)).reduce((acc, s) => acc + (s.seconds ?? 0), 0);
        const box = state === "done" ? "border-emerald-200 bg-emerald-50/50" : state === "running" ? "border-blue-300 bg-blue-50"
          : state === "failed" ? "border-rose-200 bg-rose-50" : "border-slate-200 bg-white";
        const iconTint = state === "done" ? "bg-emerald-100 text-emerald-700" : state === "running" ? "bg-blue-100 text-blue-700"
          : state === "failed" ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-500";
        const Icon = phase.icon;
        return (
          <li key={phase.name} className={`min-w-0 rounded-xl border p-3.5 ${box}`}>
            <div className="flex items-center gap-2.5">
              <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconTint}`}>
                {state === "done" ? <Check className="h-4 w-4" aria-hidden />
                  : state === "running" ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
                  : state === "failed" ? <XCircle className="h-4 w-4" aria-hidden /> : <Icon className="h-4 w-4" aria-hidden />}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-800">{i + 1}. {phase.name}</p>
                <p className="text-xs text-slate-500">
                  {state === "done" && seconds != null ? formatSeconds(seconds) : state === "running" ? "working…" : state === "failed" ? "failed" : phase.text}
                </p>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function StageIcon({ status }: { status: StageProgress["status"] }) {
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />;
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-brand-600 motion-reduce:animate-none" aria-hidden />;
  if (status === "failed") return <XCircle className="h-4 w-4 text-rose-600" aria-hidden />;
  return <Circle className="h-4 w-4 text-slate-300" aria-hidden />;
}

function RunDetails({ s }: { s: PipelineStatus }) {
  const running = s.status === "RUNNING";
  const counts = [["Input", s.input_count], ["Valid", s.valid_count], ["Rejected", s.rejected_count],
    ["Inserted", s.inserted_count], ["Updated", s.updated_count], ["Failed", s.failed_count]] as const;
  return (
    <div className="space-y-6">
      <Card title={`Run ${s.run_id}`} actions={<StatusBadge status={s.status} />}>
        <div className="flex items-center justify-between text-sm">
          <span className="text-slate-700">
            {running ? `${s.stages.find((x) => x.name === s.current_stage)?.label ?? "starting"}…` : s.status === "SUCCESS" ? "Finished" : "Finished with errors"}
          </span>
          <span className="tabular text-slate-600">{s.progress_percent}%</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={s.progress_percent}>
          <div className={`h-full rounded-full transition-all ${s.status === "FAILED" ? "bg-red-600" : s.status === "SUCCESS" ? "bg-green-600" : "bg-brand-500"}`} style={{ width: `${s.progress_percent}%` }} />
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <div><dt className="text-xs text-slate-500">Start time</dt><dd className="font-medium">{formatDateTime(s.start_time)}</dd></div>
          <div><dt className="text-xs text-slate-500">End time</dt><dd className="font-medium">{formatDateTime(s.end_time)}</dd></div>
          <div><dt className="text-xs text-slate-500">Duration</dt><dd className="tabular font-medium">{formatSeconds(s.duration_seconds)}</dd></div>
          <div><dt className="text-xs text-slate-500">Reconciliation</dt><dd>{s.reconciliation_passed == null ? "–" : <StatusBadge status={s.reconciliation_passed ? "PASS" : "FAIL"} />}</dd></div>
        </dl>
        {s.error_message && <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{s.error_message}</p>}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Step details">
          <ol className="space-y-1.5">
            {s.stages.map((st) => (
              <li key={st.name} className={`flex items-center justify-between gap-3 rounded-lg px-2.5 py-1.5 ${st.status === "running" ? "bg-blue-50" : ""}`}>
                <span className="flex items-center gap-2.5 text-sm text-slate-800"><StageIcon status={st.status} />{st.label}</span>
                <span className="tabular text-xs text-slate-500">{st.seconds == null ? "" : formatSeconds(st.seconds)}</span>
              </li>
            ))}
          </ol>
        </Card>
        <Card title="Record counts">
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {counts.map(([k, v]) => (
              <div key={k} className="rounded-lg bg-slate-50 px-3 py-2.5">
                <dt className="text-xs text-slate-500">{k}</dt>
                <dd className={`tabular text-lg font-semibold ${k === "Rejected" && v ? "text-red-700" : "text-slate-900"}`}>{formatInt(v)}</dd>
              </div>
            ))}
          </dl>
          {!running && (
            <div className="mt-5 flex flex-wrap gap-2">
              <Link to="/analytics" className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700">See analytics</Link>
              <Link to="/quality" className="rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 shadow-sm hover:bg-slate-50">Data quality</Link>
              <Link to="/reports" className="rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 shadow-sm hover:bg-slate-50">Reconciliation</Link>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function Pipeline() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run");
  const [source, setSource] = useState<DatasetSource>("generated");
  const [strict, setStrict] = useState(false);
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const watching = useRef<string | null>(null);
  const { notify } = useToast();

  usePolling(async () => {
    if (!runId) return true;
    try {
      const s = await api.pipelineStatus(runId);
      setStatus(s);
      setError(null);
      const done = s.status !== "RUNNING";
      if (done && watching.current === s.run_id) {
        watching.current = null;
        if (s.status === "SUCCESS") notify("success", "Pipeline finished", `${formatInt(s.inserted_count)} new fact rows, ${formatInt(s.rejected_count)} rejected, ${formatSeconds(s.duration_seconds)}.`);
        else notify("error", "Pipeline did not succeed", s.error_message ?? s.status);
      }
      return done;
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
      return true;
    }
  }, POLL_MS, !!runId);

  async function start() {
    setStarting(true);
    setError(null);
    setStatus(null);
    try {
      const accepted = await api.runPipeline({ source, strict_schema: strict });
      watching.current = accepted.run_id;
      notify("info", "Pipeline started", `Run ${accepted.run_id}`);
      setParams({ run: accepted.run_id });
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err));
      setError(e);
      notify("error", "Could not start the pipeline", e.message);
    } finally {
      setStarting(false);
    }
  }

  const running = status?.status === "RUNNING";
  const selected = SOURCES.find((s) => s.value === source);
  return (
    <>
      <PageHeader title="Run pipeline" description="Pick a dataset and start a run. You can follow each step live below." />
      <div className="space-y-6">
        <Card title="New run">
          <div className="grid grid-cols-1 items-end gap-4 md:grid-cols-[minmax(0,340px)_1fr_auto]">
            <div>
              <SelectField id="run-source" label="Source dataset" value={source} onChange={(e) => setSource(e.target.value as DatasetSource)}>
                {SOURCES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </SelectField>
              <p className="mt-1 text-xs text-slate-500">{selected?.hint}</p>
            </div>
            <label htmlFor="run-strict" className="flex items-center gap-2 pb-6 text-sm text-slate-700">
              <input id="run-strict" type="checkbox" checked={strict} onChange={(e) => setStrict(e.target.checked)} className="h-4 w-4 accent-brand-600" />
              Strict schema (fail on unexpected columns)
            </label>
            <div className="pb-6">
              <Button icon={<Play className="h-4 w-4" />} onClick={start} loading={starting} disabled={running} className="w-full md:w-auto">
                {running ? "Running…" : "Run pipeline"}
              </Button>
            </div>
          </div>
          <p className="flex items-center gap-1.5 text-xs text-slate-500"><Upload className="h-3.5 w-3.5" aria-hidden />
            Re-running the same files does not duplicate facts (upsert on source_system + order_item_id).</p>
        </Card>

        <Card title="Steps">
          <Stepper status={status} />
        </Card>

        {error && <ErrorState error={error} />}
        {status && <RunDetails s={status} />}
        {!status && runId && !error && <p className="text-sm text-slate-500" role="status">Loading run {runId}…</p>}
      </div>
    </>
  );
}
