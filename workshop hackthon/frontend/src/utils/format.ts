const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-IN");

export const formatInt = (v: number | null | undefined): string => (v == null ? "–" : num.format(v));
export const formatMoney = (v: number | null | undefined): string => (v == null ? "–" : inr2.format(v));
export const formatMoney0 = (v: number | null | undefined): string => (v == null ? "–" : inr.format(v));

/** Compact Indian notation: ₹14.00 Cr, ₹58.3 L, ₹12K. */
export function formatCompactMoney(v: number | null | undefined): string {
  if (v == null) return "–";
  const a = Math.abs(v);
  if (a >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `₹${(v / 1e5).toFixed(1)} L`;
  if (a >= 1e3) return `₹${(v / 1e3).toFixed(0)}K`;
  return `₹${Math.round(v)}`;
}

export function formatAxisMoney(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e7) return `₹${(v / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
  if (a >= 1e5) return `₹${(v / 1e5).toFixed(0)}L`;
  if (a >= 1e3) return `₹${(v / 1e3).toFixed(0)}K`;
  return `₹${v}`;
}

export const formatPct = (v: number | null | undefined, digits = 1): string => (v == null ? "–" : `${v.toFixed(digits)}%`);

export function formatSeconds(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v < 60) return `${v.toFixed(2)} s`;
  const m = Math.floor(v / 60);
  return `${m} min ${Math.round(v - m * 60)} s`;
}

export function formatDateTime(v: string | null | undefined): string {
  if (!v) return "–";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "medium" });
}

export function formatBytes(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(2)} MB`;
}

export const shortRunId = (id: string): string => id.replace(/^run_/, "");
