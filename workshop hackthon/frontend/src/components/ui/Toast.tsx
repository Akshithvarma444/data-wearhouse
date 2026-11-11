import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

type Tone = "success" | "error" | "info";
interface Toast { id: number; tone: Tone; title: string; message?: string }
interface ToastApi { notify: (tone: Tone, title: string, message?: string) => void }

const ToastContext = createContext<ToastApi | null>(null);

const STYLE: Record<Tone, { Icon: typeof Info; ring: string; icon: string }> = {
  success: { Icon: CheckCircle2, ring: "border-emerald-200", icon: "text-emerald-600" },
  error: { Icon: AlertTriangle, ring: "border-red-200", icon: "text-red-600" },
  info: { Icon: Info, ring: "border-brand-200", icon: "text-brand-600" },
};

/** Small notification stack (bottom-right) for results of user actions. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const notify = useCallback((tone: Tone, title: string, message?: string) => {
    const id = next.current++;
    setToasts((t) => [...t.slice(-3), { id, tone, title, message }]);
    window.setTimeout(() => dismiss(id), tone === "error" ? 9000 : 5000);
  }, [dismiss]);
  const api = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => {
          const s = STYLE[t.tone];
          return (
            <div key={t.id} role="status" className={`pointer-events-auto flex items-start gap-3 rounded-xl border bg-white p-3.5 shadow-lg ${s.ring}`}>
              <s.Icon className={`mt-0.5 h-5 w-5 shrink-0 ${s.icon}`} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-slate-900">{t.title}</p>
                {t.message && <p className="mt-0.5 text-xs text-slate-600">{t.message}</p>}
              </div>
              <button type="button" onClick={() => dismiss(t.id)} className="rounded p-0.5 text-slate-400 hover:text-slate-700" aria-label="Dismiss">
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
