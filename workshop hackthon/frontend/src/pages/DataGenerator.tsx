import { useState } from "react";
import { Download, Sparkles } from "lucide-react";
import { api } from "../api/endpoints";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Button, LinkButton } from "../components/ui/Button";
import { NumberField } from "../components/ui/Field";
import { ErrorState } from "../components/ui/States";
import { useToast } from "../components/ui/Toast";
import { formatDateTime, formatInt, formatSeconds } from "../utils/format";
import type { GenerateRequest, GenerateResponse } from "../types/api";

const LIMITS = {
  customers: [1, 500000], products: [1, 100000], orders: [1, 2000000], seed: [0, 2147483647],
} as const;

type Form = Record<"customers" | "products" | "orders" | "seed", string>;

function validate(form: Form): Partial<Record<keyof Form, string>> {
  const errors: Partial<Record<keyof Form, string>> = {};
  (Object.keys(LIMITS) as (keyof Form)[]).forEach((k) => {
    const v = Number(form[k]);
    const [min, max] = LIMITS[k];
    if (form[k].trim() === "" || !Number.isInteger(v)) errors[k] = "Enter a whole number.";
    else if (v < min || v > max) errors[k] = `Must be between ${formatInt(min)} and ${formatInt(max)}.`;
  });
  return errors;
}

export default function DataGenerator() {
  const [form, setForm] = useState<Form>({ customers: "1000", products: "200", orders: "10000", seed: "42" });
  const [includeInvalid, setIncludeInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const { notify } = useToast();
  const errors = validate(form);
  const valid = Object.keys(errors).length === 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      const body: GenerateRequest = {
        customers: Number(form.customers), products: Number(form.products), orders: Number(form.orders),
        seed: Number(form.seed), include_invalid: includeInvalid,
      };
      const generated = await api.generate(body);
      setResult(generated);
      notify("success", "Dataset generated", `${formatInt(generated.counts.order_items)} order lines in ${formatSeconds(generated.duration_seconds)}. Run the pipeline to load them.`);
    } catch (err) {
      const e2 = err instanceof Error ? err : new Error(String(err));
      setError(e2);
      notify("error", "Generation failed", e2.message);
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const estimateLines = Math.round(Number(form.orders || 0) * 2.5);

  return (
    <>
      <PageHeader title="Generate data" description="Generates customers, products, orders and order lines with Faker (en_IN). Same seed gives the same files." />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Generation settings">
          <form onSubmit={submit} noValidate className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <NumberField id="gen-customers" label="Customers" value={form.customers} onChange={set("customers")} error={errors.customers} min={1} />
              <NumberField id="gen-products" label="Products" value={form.products} onChange={set("products")} error={errors.products} min={1} />
              <NumberField id="gen-orders" label="Orders" value={form.orders} onChange={set("orders")} error={errors.orders} min={1}
                hint={`≈ ${formatInt(estimateLines)} order lines (1–6 per order)`} />
              <NumberField id="gen-seed" label="Random seed" value={form.seed} onChange={set("seed")} error={errors.seed} min={0} />
            </div>
            <label htmlFor="gen-invalid" className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 text-sm">
              <input id="gen-invalid" type="checkbox" checked={includeInvalid} onChange={(e) => setIncludeInvalid(e.target.checked)} className="mt-0.5 h-4 w-4 accent-brand-600" />
              <span><span className="font-medium text-slate-800">Also generate the invalid test dataset</span>
                <span className="block text-xs text-slate-500">Goes to datasets/invalid with a list of the injected defects.</span></span>
            </label>
            <Button type="submit" icon={<Sparkles className="h-4 w-4" />} loading={busy} disabled={!valid}>
              {busy ? "Generating…" : "Generate dataset"}
            </Button>
          </form>
        </Card>

        <Card title="Result">
          {error && <ErrorState error={error} />}
          {!error && !result && !busy && <p className="text-sm text-slate-500">Nothing generated yet.</p>}
          {busy && <p className="text-sm text-slate-600" role="status">Generating…</p>}
          {result && !busy && (
            <div className="space-y-4">
              <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">
                Generated in {formatSeconds(result.duration_seconds)} at {formatDateTime(result.generated_at)}.
              </p>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                {Object.entries(result.counts).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-slate-200 p-3">
                    <dt className="text-xs capitalize text-slate-500">{k.replace("_", " ")}</dt>
                    <dd className="tabular text-lg font-semibold text-slate-900">{formatInt(v)}</dd>
                  </div>
                ))}
              </dl>
              <p className="break-all font-mono text-xs text-slate-500">{result.output_dir}</p>
              {result.invalid_counts && (
                <p className="text-sm text-slate-600">Invalid test dataset: {formatInt(result.injected_defects)} injected defects across{" "}
                  {formatInt(Object.values(result.invalid_counts).reduce((a, b) => a + b, 0))} records.</p>
              )}
              <div className="flex flex-wrap gap-2">
                <LinkButton href={api.generatorDownloadUrl("generated")} icon={<Download className="h-4 w-4" />}>Download generated (.zip)</LinkButton>
                {result.invalid_counts && <LinkButton href={api.generatorDownloadUrl("invalid")} icon={<Download className="h-4 w-4" />}>Download invalid (.zip)</LinkButton>}
              </div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
