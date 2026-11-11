import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500" role="status">
      <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
      {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center" role="alert">
      <AlertTriangle className="h-5 w-5 text-red-600" aria-hidden />
      <p className="max-w-xl text-sm text-red-800">{error.message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      <Inbox className="h-6 w-6 text-slate-400" aria-hidden />
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {children && <div className="max-w-md text-xs text-slate-500">{children}</div>}
    </div>
  );
}

/** Renders loading / error / empty / content for one API state. */
export function AsyncContent<T>({ state, isEmpty, empty, children }: {
  state: { data: T | null; error: Error | null; loading: boolean; reload: () => void };
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (state.data === null) return <LoadingState />;
  if (isEmpty && isEmpty(state.data)) return <>{empty ?? <EmptyState title="No data yet" />}</>;
  return <>{children(state.data)}</>;
}
