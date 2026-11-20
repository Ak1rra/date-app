import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { vi } from 'vitest';
import { routes } from '../App';
import type { Session } from '../hooks/useSession';

export const signedIn: Session = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'me@example.com',
    display_name: 'Sam',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'planner',
  },
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Stubs fetch so GET /api/v1/auth/session resolves to `session` (or rejects with `session`). */
export function mockSession(session: Session | Error | Response) {
  const fetchMock = vi.fn<typeof fetch>(async () => {
    if (session instanceof Error) throw session;
    if (session instanceof Response) return session.clone();
    return jsonResponse(session);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
