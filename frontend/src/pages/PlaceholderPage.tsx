import type { ReactNode } from 'react';

/** Minimal full-screen frame for pages that a later task wires to the API client. */
export function PlaceholderPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 bg-canvas p-6 text-ink">
      <h1 className="text-3xl font-bold">{title}</h1>
      <p className="text-muted">{children}</p>
    </main>
  );
}
