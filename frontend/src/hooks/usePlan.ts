import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { itinerariesKey, type ItineraryOut } from './useItineraries';
import { useViewerId } from './useSession';

export type PlanSessionIn = Schemas['PlanSessionIn'];
export type PlanSessionOut = Schemas['PlanSessionOut'];
export type PlanOptionsOut = Schemas['PlanOptionsOut'];
export type PlanOptionOut = Schemas['PlanOptionOut'];
export type PlanOptionStop = Schemas['PlanOptionStop'];
export type PlanBuildIn = Schemas['PlanBuildIn'];

const planKey = (userId: string | undefined) => ['plan-sessions', userId] as const;

/** GET /plan/sessions: used to offer "Pick up where you left off". */
export function usePlanSessions() {
  const userId = useViewerId();
  return useQuery({
    queryKey: planKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/plan/sessions')),
  });
}

/** What a first-step mutation needs: the inputs, plus a session a failed attempt already made. */
export interface StartPlanVars {
  body: PlanSessionIn;
  /** Reuse this session instead of creating one (a retry with unchanged inputs). */
  sessionId?: string;
  /** Called as soon as a session exists, so the caller can keep it if a later step fails. */
  onSession?: (id: string) => void;
}

async function sessionFor({ body, sessionId, onSession }: StartPlanVars): Promise<string> {
  if (sessionId) return sessionId;
  const created: PlanSessionOut = unwrap(await client.POST('/api/v1/plan/sessions', { body }));
  onSession?.(created.id);
  return created.id;
}

function usePlanInvalidation() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return (itineraryDone: boolean) => {
    void queryClient.invalidateQueries({ queryKey: planKey(userId) });
    if (itineraryDone) void queryClient.invalidateQueries({ queryKey: itinerariesKey(userId) });
  };
}

/** Create a session, then ask for its first three options. */
export function useShowOptions() {
  const invalidate = usePlanInvalidation();
  return useMutation({
    mutationFn: async (vars: StartPlanVars): Promise<PlanOptionsOut> => {
      const planId = await sessionFor(vars);
      return unwrap(
        await client.POST('/api/v1/plan/sessions/{plan_id}/options', {
          params: { path: { plan_id: planId } },
          body: { more: false },
        }),
      );
    },
    onSettled: () => invalidate(false),
  });
}

/** Create a session and let the planner build the whole date from it. */
export function usePlanAll() {
  const invalidate = usePlanInvalidation();
  return useMutation({
    mutationFn: async (vars: StartPlanVars): Promise<ItineraryOut> => {
      const planId = await sessionFor(vars);
      return unwrap(
        await client.POST('/api/v1/plan/sessions/{plan_id}/plan-all', {
          params: { path: { plan_id: planId } },
        }),
      );
    },
    onSettled: (data) => invalidate(!!data),
  });
}

/** POST /plan/sessions/{id}/options {more: true}: the response holds all options so far. */
export function useMoreOptions(id: string | undefined) {
  const invalidate = usePlanInvalidation();
  return useMutation({
    mutationFn: async (): Promise<PlanOptionsOut> =>
      unwrap(
        await client.POST('/api/v1/plan/sessions/{plan_id}/options', {
          params: { path: { plan_id: id! } },
          body: { more: true },
        }),
      ),
    onSettled: () => invalidate(false),
  });
}

/** POST /plan/sessions/{id}/build: turn one to three chosen options into an itinerary. */
export function useBuildPlan(id: string | undefined) {
  const invalidate = usePlanInvalidation();
  return useMutation({
    mutationFn: async (body: PlanBuildIn): Promise<ItineraryOut> =>
      unwrap(
        await client.POST('/api/v1/plan/sessions/{plan_id}/build', {
          params: { path: { plan_id: id! } },
          body,
        }),
      ),
    onSettled: (data) => invalidate(!!data),
  });
}
