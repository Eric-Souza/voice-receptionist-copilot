import type { ReactNode } from "react";

export function Panel({ title, aside, children, className = "" }: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`flex min-h-0 flex-col rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <h2 className="text-sm font-semibold tracking-wide text-slate-700 uppercase">{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}
