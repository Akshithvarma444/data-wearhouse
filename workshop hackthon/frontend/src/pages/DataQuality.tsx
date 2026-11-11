import { useState } from "react";
import { AlertOctagon, CheckCircle2, Copy, FileWarning, Link2Off, ShieldAlert, SearchX } from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { StatCard } from "../components/ui/StatCard";
import { AsyncContent, EmptyState } from "../components/ui/States";
import { DataTable } from "../components/ui/DataTable";
import { HBarChart } from "../components/ui/charts";
import { SelectField } from "../components/ui/Field";
import { Button } from "../components/ui/Button";
import { formatDateTime, formatInt } from "../utils/format";
import type { RejectedRecord } from "../types/api";

const PAGE = 25;

function Rejections({ reasons }: { reasons: string[] }) {
  const [entity, setEntity] = useState("");
  const [reason, setReason] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const state = useApi((signal) => api.rejections({ entity, reason, limit: PAGE, offset }, signal), [entity, reason, offset]);

  return (
    <Card title="Quarantined records" description="ops.rejected_records">
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:w-2/3">
        <SelectField id="rej-entity" label="Entity" value={entity} onChange={(e) => { setEntity(e.target.value); setOffset(0); }}>
          <option value="">All entities</option>
          {["customers", "products", "orders", "order_items"].map((e) => <option key={e} value={e}>{e}</option>)}
        </SelectField>
        <SelectField id="rej-reason" label="Reason code" value={reason} onChange={(e) => { setReason(e.target.value); setOffset(0); }}>
          <option value="">All reasons</option>
          {reasons.map((r) => <option key={r} value={r}>{r}</option>)}
        </SelectField>
      </div>
      <AsyncContent state={state} isEmpty={(d) => d.total === 0} empty={<EmptyState title="No rejected records match these filters" />}>
        {(page) => (
          <>
            <DataTable<RejectedRecord> rows={page.items} rowKey={(r) => String(r.reject_id)} maxHeight="560px" columns={[
              { key: "entity_name", header: "Entity" },
              { key: "source_file", header: "File / line", render: (r) => <span className="font-mono text-xs">{r.source_file}:{r.source_row_number}</span> },
              { key: "source_record_id", header: "Record id", render: (r) => <span className="font-mono text-xs">{r.source_record_id ?? "(blank)"}</span> },
              { key: "reason_codes", header: "Reasons", render: (r) => <div className="flex flex-wrap gap-1">{r.reason_codes.map((c) => <span key={c} className="rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700">{c}</span>)}</div> },
              { key: "rejection_reason", header: "Details", wrap: true, render: (r) => (
                <div>
                  <p>{r.rejection_reason}</p>
                  <button type="button" className="mt-1 text-xs font-medium text-brand-600 hover:underline" onClick={() => setOpen(open === r.reject_id ? null : r.reject_id)}>
                    {open === r.reject_id ? "Hide source record" : "Show source record"}
                  </button>
                  {open === r.reject_id && <pre className="mt-2 max-w-xl overflow-x-auto rounded bg-slate-50 p-2 text-[11px]">{JSON.stringify(r.raw_record, null, 2)}</pre>}
                </div>) },
              { key: "rejected_at", header: "Rejected at", render: (r) => formatDateTime(r.rejected_at) },
            ]} />
            <div className="mt-3 flex items-center justify-between text-xs text-slate-600">
              <span className="tabular">{formatInt(page.offset + 1)}–{formatInt(Math.min(page.offset + page.items.length, page.total))} of {formatInt(page.total)}</span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                <Button variant="secondary" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
              </div>
            </div>
          </>
        )}
      </AsyncContent>
    </Card>
  );
}

export default function DataQuality() {
  const state = useApi((signal) => api.qualitySummary(signal));
  return (
    <>
      <PageHeader title="Data quality" description="Records rejected during validation, across all runs." />
      <AsyncContent state={state}>
        {(q) => (
          <div className="space-y-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard icon={CheckCircle2} label="Valid records" value={formatInt(q.valid_records)} hint={`of ${formatInt(q.total_records)} input records`} tone="good" />
              <StatCard icon={ShieldAlert} label="Rejected records" value={formatInt(q.rejected_records)} tone={q.rejected_records ? "bad" : "neutral"} />
              <StatCard icon={SearchX} label="Missing values" value={formatInt(q.missing_values)} hint="required field empty" tone="neutral" />
              <StatCard icon={Copy} label="Duplicate records" value={formatInt(q.duplicate_records)} hint="exact + conflicting" tone="neutral" />
              <StatCard icon={Link2Off} label="Invalid references" value={formatInt(q.invalid_references)} hint="unknown customer / order / product" tone="neutral" />
              <StatCard icon={FileWarning} label="Schema validation errors" value={formatInt(q.schema_errors)} hint="type, format, allowed values, malformed rows" tone="neutral" />
              <StatCard icon={AlertOctagon} label="Business rule errors" value={formatInt(q.business_rule_errors)} hint="negative values, discount > gross, dates" tone="neutral" />
            </div>
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <Card title="Rejections by reason code">
                {q.by_reason.length ? <HBarChart data={q.by_reason} labelKey="name" valueKey="value" money={false} /> : <EmptyState title="No rejected records" />}
              </Card>
              <Card title="Rejected records by entity">
                {q.by_entity.length ? <HBarChart data={q.by_entity} labelKey="name" valueKey="value" money={false} /> : <EmptyState title="No rejected records" />}
              </Card>
            </div>
            <Rejections reasons={q.by_reason.map((r) => r.name)} />
          </div>
        )}
      </AsyncContent>
    </>
  );
}
