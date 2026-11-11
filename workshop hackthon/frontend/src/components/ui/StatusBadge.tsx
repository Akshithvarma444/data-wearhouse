import { CheckCircle2, CircleDashed, Info, Loader2, XCircle } from "lucide-react";

const styles: Record<string, { cls: string; Icon: typeof CheckCircle2; label?: string }> = {
  SUCCESS: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: CheckCircle2 },
  PASS: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: CheckCircle2 },
  ready: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: CheckCircle2, label: "Ready" },
  done: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: CheckCircle2, label: "Done" },
  FAILED: { cls: "bg-red-50 text-red-700 ring-red-200", Icon: XCircle },
  FAIL: { cls: "bg-red-50 text-red-700 ring-red-200", Icon: XCircle },
  failed: { cls: "bg-red-50 text-red-700 ring-red-200", Icon: XCircle, label: "Failed" },
  invalid: { cls: "bg-red-50 text-red-700 ring-red-200", Icon: XCircle, label: "Invalid" },
  RECONCILIATION_FAILED: { cls: "bg-amber-50 text-amber-800 ring-amber-200", Icon: XCircle, label: "Reconciliation failed" },
  RUNNING: { cls: "bg-brand-50 text-brand-700 ring-brand-100", Icon: Loader2, label: "Running" },
  running: { cls: "bg-brand-50 text-brand-700 ring-brand-100", Icon: Loader2, label: "Running" },
  pending: { cls: "bg-slate-100 text-slate-600 ring-slate-200", Icon: CircleDashed, label: "Pending" },
  missing: { cls: "bg-slate-100 text-slate-600 ring-slate-200", Icon: CircleDashed, label: "Not uploaded" },
  INFO: { cls: "bg-slate-100 text-slate-600 ring-slate-200", Icon: Info, label: "Info" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = styles[status] ?? { cls: "bg-slate-100 text-slate-700 ring-slate-200", Icon: Info };
  const spin = s.Icon === Loader2 ? "animate-spin motion-reduce:animate-none" : "";
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${s.cls}`}>
      <s.Icon className={`h-3.5 w-3.5 ${spin}`} aria-hidden />
      {s.label ?? status}
    </span>
  );
}
