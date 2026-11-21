import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { itinerariesKey } from './useItineraries';
import { useViewerId } from './useSession';

export type TemplateOut = Schemas['TemplateOut'];
export type TemplateReplanIn = Schemas['TemplateReplanIn'];

export const templatesKey = (userId: string | undefined) => ['templates', userId] as const;

/** GET /templates: the planner's saved date templates. */
export function useTemplates() {
  const userId = useViewerId();
  return useQuery({
    queryKey: templatesKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/templates')),
  });
}

/** POST /itineraries/{id}/template: saves the itinerary's shape under a name. */
export function useSaveTemplate(itineraryId: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/template', {
          params: { path: { itinerary_id: itineraryId } },
          body: { name },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: templatesKey(userId) }),
  });
}

/** DELETE /templates/{id}. */
export function useDeleteTemplate() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/templates/{template_id}', {
          params: { path: { template_id: id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: templatesKey(userId) }),
  });
}

/** POST /templates/{id}/replan: a fresh itinerary from a template at a new time. */
export function useReplanTemplate() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: TemplateReplanIn }) =>
      unwrap(
        await client.POST('/api/v1/templates/{template_id}/replan', {
          params: { path: { template_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: itinerariesKey(userId) }),
  });
}
