import type { ReactNode } from 'react';

export default function PageShell({
  eyebrow,
  title,
  description,
  aside,
  children,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-6">
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="rounded-2xl border border-line-700/70 bg-ink-900 px-6 py-6">
          {eyebrow && (
            <div className="mb-3 inline-flex rounded-full border border-line-700/80 bg-ink-850 px-3 py-1 text-xs font-medium text-slate-300">
              {eyebrow}
            </div>
          )}
          <h2 className="font-display text-2xl font-semibold text-white sm:text-3xl">
            {title}
          </h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
            {description}
          </p>
        </div>
        {aside && (
          <aside className="rounded-2xl border border-line-700/70 bg-ink-900 p-5">
            {aside}
          </aside>
        )}
      </section>
      {children}
    </div>
  );
}
