import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  description?: string;
  icon?: LucideIcon;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}

export function Card({ title, description, icon: Icon, actions, children, className = "", bodyClassName = "p-5" }: CardProps) {
  return (
    <section className={`min-w-0 rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4">
          <div className="flex min-w-0 items-center gap-2.5">
            {Icon && <Icon className="h-4 w-4 shrink-0 text-slate-400" aria-hidden />}
            <div className="min-w-0">
              {title && <h2 className="text-[15px] font-semibold text-slate-800">{title}</h2>}
              {description && <p className="text-xs text-slate-500">{description}</p>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}
