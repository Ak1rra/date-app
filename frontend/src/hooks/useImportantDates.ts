import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { useViewerId } from './useSession';

export type ImportantDateOut = Schemas['ImportantDateOut'];
export type ImportantDateIn = Schemas['ImportantDateIn'];

const datesKey = (userId: string | undefined) => ['important-dates', userId] as const;

/** GET /couple/important-dates */
export function useImportantDates() {
  const userId = useViewerId();
  return useQuery({
    queryKey: datesKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/couple/important-dates')),
  });
}

/** POST (no id) or PATCH (with id) /couple/important-dates */
export function useSaveImportantDate() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id?: string; body: ImportantDateIn }) =>
      id
        ? unwrap(
            await client.PATCH('/api/v1/couple/important-dates/{date_id}', {
              params: { path: { date_id: id } },
              body,
            }),
          )
        : unwrap(await client.POST('/api/v1/couple/important-dates', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: datesKey(userId) }),
  });
}

/** DELETE /couple/important-dates/{id} */
export function useDeleteImportantDate() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/couple/important-dates/{date_id}', {
          params: { path: { date_id: id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: datesKey(userId) }),
  });
}
