import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { formatAxisMoney, formatInt, formatMoney0 } from "../../utils/format";

export const SERIES = "#2a78d6";
const GRID = "#e2e8f0";
const TICK = { fontSize: 11, fill: "#64748b" };

interface TipPayload { value?: number | string; payload?: Record<string, unknown> }

function ChartTooltip({ active, payload, label, format }: { active?: boolean; payload?: TipPayload[]; label?: string | number; format: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-md">
      <p className="font-semibold text-slate-900">{String(label ?? "")}</p>
      <p className="tabular mt-0.5 text-slate-600">{format(Number(payload[0].value ?? 0))}</p>
    </div>
  );
}

/** Single-series area/line chart over time. */
export function TrendChart<T extends object>({ data, xKey, yKey, height = 280, money = true }: {
  data: T[]; xKey: keyof T & string; yKey: keyof T & string; height?: number; money?: boolean;
}) {
  const fmt = money ? formatMoney0 : formatInt;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <defs>
          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={SERIES} stopOpacity={0.18} />
            <stop offset="100%" stopColor={SERIES} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} tick={TICK} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={24} />
        <YAxis tick={TICK} tickLine={false} axisLine={false} width={64} tickFormatter={money ? formatAxisMoney : formatInt} />
        <Tooltip content={<ChartTooltip format={fmt} />} cursor={{ stroke: "#94a3b8" }} />
        <Area type="monotone" dataKey={yKey} stroke={SERIES} strokeWidth={2} fill="url(#trendFill)" activeDot={{ r: 5 }} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Horizontal single-series bar chart (category comparisons). */
export function HBarChart<T extends object>({ data, labelKey, valueKey, money = true, height, colors }: {
  data: T[]; labelKey: keyof T & string; valueKey: keyof T & string; money?: boolean; height?: number; colors?: string[];
}) {
  const fmt = money ? formatMoney0 : formatInt;
  const h = height ?? Math.max(160, data.length * 34 + 40);
  const longest = Math.max(0, ...data.map((d) => String(d[labelKey] ?? "").length));
  const labelWidth = Math.min(230, Math.max(70, Math.round(longest * 7.2) + 16));
  return (
    <ResponsiveContainer width="100%" height={h}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }} barCategoryGap={8}>
        <CartesianGrid stroke={GRID} horizontal={false} />
        <XAxis type="number" tick={TICK} tickLine={false} axisLine={{ stroke: GRID }} tickFormatter={money ? formatAxisMoney : formatInt} />
        <YAxis type="category" dataKey={labelKey} tick={TICK} tickLine={false} axisLine={false} width={labelWidth} interval={0} />
        <Tooltip content={<ChartTooltip format={fmt} />} cursor={{ fill: "rgba(42,120,214,0.06)" }} />
        <Bar dataKey={valueKey} fill={SERIES} radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false}>
          {colors && data.map((_, i) => <Cell key={i} fill={colors[i % colors.length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Vertical single-series column chart (few categories, e.g. years). */
export function ColumnChart<T extends object>({ data, labelKey, valueKey, money = true, height = 240 }: {
  data: T[]; labelKey: keyof T & string; valueKey: keyof T & string; money?: boolean; height?: number;
}) {
  const fmt = money ? formatMoney0 : formatInt;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={labelKey} tick={TICK} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis tick={TICK} tickLine={false} axisLine={false} width={64} tickFormatter={money ? formatAxisMoney : formatInt} />
        <Tooltip content={<ChartTooltip format={fmt} />} cursor={{ fill: "rgba(42,120,214,0.06)" }} />
        <Bar dataKey={valueKey} fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={56} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
