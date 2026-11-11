import { useState } from "react";
import { KeyRound, Link as LinkIcon } from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { AsyncContent } from "../components/ui/States";
import { DataTable } from "../components/ui/DataTable";
import { formatInt } from "../utils/format";
import type { WarehouseColumn, WarehouseTable } from "../types/api";

const KIND_STYLE: Record<WarehouseTable["kind"], string> = {
  fact: "bg-brand-50 text-brand-700",
  dimension: "bg-indigo-50 text-indigo-700",
  source: "bg-slate-100 text-slate-700",
  operational: "bg-amber-50 text-amber-800",
};

function cell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function TableDetail({ name }: { name: string }) {
  const state = useApi((signal) => api.warehouseTable(name, 10, signal), [name]);
  return (
    <AsyncContent state={state}>
      {(t) => (
        <div className="space-y-6">
          <Card title={`${t.schema}.${t.name}`} description={`${t.description} · ${formatInt(t.row_count)} rows`}>
            <DataTable<WarehouseColumn> rows={t.columns} rowKey={(c) => c.name} columns={[
              { key: "name", header: "Column", render: (c) => (
                <span className="flex items-center gap-1.5 font-mono text-xs">
                  {c.is_primary_key && <KeyRound className="h-3.5 w-3.5 text-amber-600" aria-label="primary key" />}
                  {c.foreign_key && <LinkIcon className="h-3.5 w-3.5 text-brand-600" aria-label="foreign key" />}
                  {c.name}
                </span>) },
              { key: "data_type", header: "Data type", render: (c) => <span className="font-mono text-xs">{c.data_type}</span> },
              { key: "nullable", header: "Nullable", render: (c) => (c.nullable ? "yes" : "no") },
              { key: "is_primary_key", header: "Primary key", render: (c) => (c.is_primary_key ? "PK" : "") },
              { key: "foreign_key", header: "Foreign key", render: (c) => <span className="font-mono text-xs">{c.foreign_key ?? ""}</span> },
            ]} />
          </Card>
          <Card title="Sample rows" description="First 10 rows">
            {t.sample_rows.length ? (
              <DataTable rows={t.sample_rows} rowKey={(_, i) => String(i)}
                columns={Object.keys(t.sample_rows[0]).map((k) => ({ key: k, header: k, render: (r: Record<string, unknown>) => <span className="text-xs">{cell(r[k])}</span> }))} />
            ) : <p className="text-sm text-slate-500">The table is empty. Run the pipeline to populate it.</p>}
          </Card>
        </div>
      )}
    </AsyncContent>
  );
}

export default function DataWarehouse() {
  const state = useApi((signal) => api.warehouseTables(signal));
  const [selected, setSelected] = useState("fact_sales");
  return (
    <>
      <PageHeader title="Warehouse tables" description="Click a table to see its columns, keys and a few sample rows." />
      <AsyncContent state={state}>
        {(tables) => (
          <div className="space-y-6">
            {(["fact", "dimension", "source", "operational"] as const).map((kind) => {
              const group = tables.filter((t) => t.kind === kind);
              if (!group.length) return null;
              return (
                <div key={kind}>
                  <h2 className="mb-2 text-xs font-medium text-slate-500">{kind === "fact" ? "Fact" : kind === "dimension" ? "Dimensions" : kind === "source" ? "Source (ODS)" : "Operational"}</h2>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {group.map((t) => (
                      <button key={t.name} type="button" onClick={() => setSelected(t.name)}
                        className={`min-w-0 rounded-md border bg-white p-3 text-left hover:border-brand-500 ${selected === t.name ? "border-brand-500 bg-brand-50" : "border-slate-200"}`}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-sm font-semibold text-slate-900">{t.name}</span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${KIND_STYLE[t.kind]}`}>{t.kind}</span>
                        </div>
                        <p className="tabular mt-1 text-lg font-semibold text-slate-900">{formatInt(t.row_count)} <span className="text-xs font-normal text-slate-500">rows</span></p>
                        <p className="mt-1 line-clamp-2 text-xs text-slate-500">{t.description}</p>
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
            <TableDetail name={selected} />
          </div>
        )}
      </AsyncContent>
    </>
  );
}
