import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string;
  label: string;
  error?: string | null;
  hint?: string;
}

export function NumberField({ id, label, error, hint, ...rest }: FieldProps) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-xs font-medium text-slate-700">{label}</label>
      <input id={id} type="number" aria-invalid={!!error} aria-describedby={`${id}-help`}
        className={`tabular mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 ${error ? "border-red-400" : "border-slate-300"}`}
        {...rest} />
      <p id={`${id}-help`} className={`mt-1 text-xs ${error ? "text-red-600" : "text-slate-500"}`}>{error ?? hint ?? " "}</p>
    </div>
  );
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  id: string;
  label: string;
  children: ReactNode;
}

export function SelectField({ id, label, children, ...rest }: SelectProps) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-xs font-medium text-slate-700">{label}</label>
      <select id={id}
        className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
        {...rest}>
        {children}
      </select>
    </div>
  );
}
