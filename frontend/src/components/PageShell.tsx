import type { ReactNode } from 'react';

export default function PageShell({
  eyebrow,
  title,
  description,
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
      <section>
        {eyebrow && (
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent">
            {eyebrow}
          </div>
        )}
        <h2 className="font-display text-2xl font-bold text-text sm:text-3xl">
          {title}
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">
          {description}
        </p>
      </section>
      {children}
    </div>
  );
}
