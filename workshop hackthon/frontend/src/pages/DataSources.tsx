import { useState } from "react";
import { FileUp, RefreshCw } from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { AsyncContent } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { SelectField } from "../components/ui/Field";
import { useToast } from "../components/ui/Toast";
import { formatBytes, formatDateTime, formatInt } from "../utils/format";
import type { DatasetCollection, DatasetFile, DatasetSource, EntityName } from "../types/api";

const ENTITIES: EntityName[] = ["customers", "products", "orders", "order_items"];
const SOURCE_LABEL: Record<DatasetSource, string> = {
  uploads: "Uploaded files", generated: "Generated dataset", sample: "Sample dataset (committed)", invalid: "Invalid test dataset",
};
const MAX_MB = 200;

function UploadPanel({ onUploaded }: { onUploaded: () => void }) {
  const [entity, setEntity] = useState<EntityName>("customers");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const { notify } = useToast();

  const validationError = !file ? null
    : !file.name.toLowerCase().endsWith(".csv") ? "Only .csv files are accepted."
    : file.size === 0 ? "The file is empty."
    : file.size > MAX_MB * 1024 * 1024 ? `The file is larger than ${MAX_MB} MB.` : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || validationError) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.uploadDataset(entity, file);
      setMessage({ ok: result.status === "ready", text: result.status === "ready"
        ? `Uploaded ${result.file_name}: ${formatInt(result.record_count)} records, schema OK.`
        : `Uploaded, but the file is not usable: ${result.message ?? "schema problem"}` });
      setFile(null);
      (e.target as HTMLFormElement).reset();
      notify(result.status === "ready" ? "success" : "error", result.status === "ready" ? "File uploaded" : "Upload has problems", `${result.file_name}: ${formatInt(result.record_count)} records`);
      onUploaded();
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      setMessage({ ok: false, text });
      notify("error", "Upload rejected", text);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Upload CSV" description="Saved as datasets/uploads/<entity>.csv. Load them with source 'Uploaded files' on the Pipeline page.">
      <form onSubmit={submit} className="grid grid-cols-1 items-end gap-4 md:grid-cols-[200px_1fr_auto]">
        <SelectField id="upload-entity" label="Entity" value={entity} onChange={(e) => setEntity(e.target.value as EntityName)}>
          {ENTITIES.map((e) => <option key={e} value={e}>{e} ({e}.csv)</option>)}
        </SelectField>
        <div className="min-w-0">
          <label htmlFor="upload-file" className="block text-xs font-medium text-slate-700">CSV file</label>
          <input id="upload-file" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-brand-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100" />
        </div>
        <Button type="submit" icon={<FileUp className="h-4 w-4" />} loading={busy} disabled={!file || !!validationError}>Upload</Button>
      </form>
      {file && <p className="mt-2 text-xs text-slate-500">{file.name} · {formatBytes(file.size)}</p>}
      {validationError && <p className="mt-2 text-xs text-red-600" role="alert">{validationError}</p>}
      {message && <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${message.ok ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`} role="status">{message.text}</p>}
    </Card>
  );
}

function FileCard({ f }: { f: DatasetFile }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold capitalize text-slate-900">{f.entity.replace("_", " ")}</p>
          <p className="truncate font-mono text-xs text-slate-500">{f.file_name}</p>
        </div>
        <StatusBadge status={f.status} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div><dt className="text-slate-500">Records</dt><dd className="tabular font-medium text-slate-800">{formatInt(f.record_count)}</dd></div>
        <div><dt className="text-slate-500">Size</dt><dd className="tabular font-medium text-slate-800">{formatBytes(f.size_bytes)}</dd></div>
        <div className="min-w-0"><dt className="text-slate-500">Modified</dt><dd className="truncate text-slate-800" title={formatDateTime(f.modified_at)}>{formatDateTime(f.modified_at)}</dd></div>
      </dl>
      {f.message && <p className="mt-2 text-xs text-red-700">{f.message}</p>}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs font-medium text-brand-600">Schema ({f.columns.length} columns)</summary>
        <ul className="mt-2 space-y-1 text-xs">
          {f.columns.map((c) => {
            const present = f.found_columns.includes(c.name);
            return (
              <li key={c.name} className="flex items-center justify-between gap-2">
                <span className="font-mono text-slate-700">{c.name}</span>
                <span className="flex items-center gap-2 text-slate-500">
                  {c.type}{c.required && <span className="rounded bg-slate-100 px-1 text-[10px]">required</span>}
                  {f.exists && !present && <span className="text-red-600">missing</span>}
                </span>
              </li>
            );
          })}
          {f.unexpected_columns.map((c) => <li key={c} className="font-mono text-amber-700">{c} <span className="font-sans">(unexpected)</span></li>)}
        </ul>
      </details>
    </div>
  );
}

function Collection({ c }: { c: DatasetCollection }) {
  return (
    <Card title={SOURCE_LABEL[c.source]} description={c.directory}>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {c.files.map((f) => <FileCard key={f.entity} f={f} />)}
      </div>
    </Card>
  );
}

export default function DataSources() {
  const state = useApi((signal) => api.datasets(signal));
  return (
    <>
      <PageHeader title="Data sources" description="Your four CSV files: customers, products, orders and order items."
        actions={<Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={state.reload}>Refresh</Button>} />
      <div className="space-y-6">
        <UploadPanel onUploaded={state.reload} />
        <AsyncContent state={state}>{(d) => <div className="space-y-6">{d.sources.map((c) => <Collection key={c.source} c={c} />)}</div>}</AsyncContent>
      </div>
    </>
  );
}
