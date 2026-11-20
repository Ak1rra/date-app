import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { useViewerId } from './useSession';

export type ConstraintsOut = Schemas['ConstraintsOut'];
export type ConstraintsIn = Schemas['ConstraintsIn'];

const constraintsKey = (userId: string | undefined) => ['constraints', userId] as const;

/** GET /couple/constraints */
export function useConstraints() {
  const userId = useViewerId();
  return useQuery({
    queryKey: constraintsKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/couple/constraints')),
  });
}

/** PUT /couple/constraints (full replace) */
export function useSaveConstraints() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ConstraintsIn) =>
      unwrap(await client.PUT('/api/v1/couple/constraints', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: constraintsKey(userId) }),
  });
}
