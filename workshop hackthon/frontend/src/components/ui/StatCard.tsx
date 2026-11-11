import { TrendingDown, TrendingUp, type LucideIcon } from "lucide-react";

type Tone = "brand" | "good" | "bad" | "neutral" | "violet" | "amber";

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  tone?: Tone;
  percent?: number | null;
  /** optional change in percent, shown as a small up/down chip */
  delta?: { value: number; label: string } | null;
}

const TINT: Record<Tone, { icon: string; bar: string }> = {
  brand: { icon: "bg-blue-50 text-blue-600", bar: "bg-blue-500" },
  good: { icon: "bg-emerald-50 text-emerald-600", bar: "bg-emerald-500" },
  bad: { icon: "bg-rose-50 text-rose-600", bar: "bg-rose-500" },
  neutral: { icon: "bg-slate-100 text-slate-600", bar: "bg-slate-400" },
  violet: { icon: "bg-violet-50 text-violet-600", bar: "bg-violet-500" },
  amber: { icon: "bg-amber-50 text-amber-600", bar: "bg-amber-500" },
};

export function StatCard({ label, value, hint, icon: Icon, tone = "neutral", percent, delta }: StatCardProps) {
  const t = TINT[tone];
  const up = (delta?.value ?? 0) >= 0;
  return (
    <div className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-3">
        {Icon && (
          <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${t.icon}`}>
            <Icon className="h-5 w-5" aria-hidden />
          </span>
        )}
        <div className="min-w-0">
          <p className="truncate text-[13px] text-slate-500">{label}</p>
          <p className="tabular truncate text-[22px] font-semibold leading-tight text-slate-900" title={value}>{value}</p>
        </div>
      </div>
      {percent != null && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div className={`h-full rounded-full ${t.bar}`} style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
        </div>
      )}
      {(hint || delta) && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          {delta && (
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${up ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
              {up ? <TrendingUp className="h-3 w-3" aria-hidden /> : <TrendingDown className="h-3 w-3" aria-hidden />}
              {up ? "+" : ""}{delta.value.toFixed(1)}%
            </span>
          )}
          {delta && <span>{delta.label}</span>}
          {hint && <span className="truncate" title={hint}>{hint}</span>}
        </div>
      )}
    </div>
  );
}
