import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  children: ReactNode;
  action?: ReactNode;
  headingLevel?: 2 | 3;
}

/** A teaching empty state (UX-6): what lives here, why it is empty, and what to do next. */
export function EmptyState({ icon, title, children, action, headingLevel = 3 }: EmptyStateProps) {
  const Heading = `h${headingLevel}` as const;
  return (
    <div className="flex flex-col items-center gap-3 px-4 py-6 text-center">
      {icon ? (
        <span className="flex size-12 items-center justify-center rounded-full bg-accent-soft text-accent">
          {icon}
        </span>
      ) : null}
      <Heading className="text-lg font-semibold">{title}</Heading>
      <p className="max-w-prose text-muted">{children}</p>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

export function Card({
  title,
  children,
  className,
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-3xl border border-line bg-surface p-4 shadow-sm ${className ?? ''}`}
      aria-label={title}
    >
      {title ? (
        <h2 className="px-2 pt-1 text-sm font-semibold tracking-wide text-muted uppercase">
          {title}
        </h2>
      ) : null}
      {children}
    </section>
  );
}
