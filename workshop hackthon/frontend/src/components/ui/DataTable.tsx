import type { ReactNode } from "react";
import { EmptyState } from "./States";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  align?: "left" | "right";
  wrap?: boolean;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  emptyTitle?: string;
  maxHeight?: string;
}

export function DataTable<T>({ columns, rows, rowKey, emptyTitle = "No rows", maxHeight }: DataTableProps<T>) {
  if (!rows.length) return <EmptyState title={emptyTitle} />;
  return (
    <div className="overflow-auto rounded-lg border border-slate-200" style={maxHeight ? { maxHeight } : undefined}>
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="sticky top-0 bg-slate-50">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col"
                className={`whitespace-nowrap px-3 py-2.5 text-xs font-medium text-slate-500 ${c.align === "right" ? "text-right" : "text-left"}`}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)} className="hover:bg-slate-50">
              {columns.map((c) => (
                <td key={c.key}
                  className={`px-3 py-2.5 align-top text-slate-700 ${c.align === "right" ? "tabular text-right" : ""} ${c.wrap ? "min-w-64 whitespace-normal" : "whitespace-nowrap"}`}>
                  {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
