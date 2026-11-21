import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
import { useViewerId } from './useSession';

export type IdeaOut = Schemas['IdeaOut'];
export type IdeaIn = Schemas['IdeaIn'];
export type IdeaPatch = Schemas['IdeaPatch'];
export type IdeaListsOut = Schemas['IdeaListsOut'];
export type VoteValue = Schemas['VoteIn']['value'];

const ideasKey = (userId: string | undefined) => ['ideas', userId] as const;

/** Both partners said yes: the vote response is the idea with the partner's vote now revealed. */
export function isMatch(idea: IdeaOut): boolean {
  return idea.votes.mine === 'yes' && idea.votes.partner === 'yes';
}

/** GET /deck: the ideas I have not voted on yet, plus whether my partner has joined. */
export function useDeck() {
  const userId = useViewerId();
  return useQuery({
    queryKey: [...ideasKey(userId), 'deck'],
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/deck')),
  });
}

/** GET /ideas/lists: matches, maybes, waiting on my partner, and passed. */
export function useIdeaLists() {
  const userId = useViewerId();
  return useQuery({
    queryKey: [...ideasKey(userId), 'lists'],
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/ideas/lists')),
  });
}

/** GET /ideas?visibility=private&status=active: only my own private ideas. */
export function useMyIdeas() {
  const userId = useViewerId();
  return useQuery({
    queryKey: [...ideasKey(userId), 'mine'],
    enabled: !!userId,
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/ideas', {
          params: { query: { visibility: 'private', status: 'active' } },
        }),
      ),
  });
}

/**
 * PUT /ideas/{id}/vote: the response is the idea; both votes "yes" means a match. A 409 means the
 * idea is locked (scheduled) or no longer active, so the lists are refetched to show where it is now.
 */
export function useVote() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, value }: { id: string; value: VoteValue }) =>
      unwrap(
        await client.PUT('/api/v1/ideas/{idea_id}/vote', {
          params: { path: { idea_id: id } },
          body: { value },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ideasKey(userId) }),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ideasKey(userId) });
      }
    },
  });
}

/**
 * POST /deck/undo: takes back my latest vote (under ten minutes old, not locked). Resolves to the
 * idea whose vote was taken back, or null (a 204) when there was nothing to undo.
 */
export function useUndoVote() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<IdeaOut | null> => {
      const result = await client.POST('/api/v1/deck/undo');
      if (result.response.status === 204) return null;
      return unwrap(result);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ideasKey(userId) }),
  });
}

/** POST /ideas: a new private idea. */
export function useCreateIdea() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: IdeaIn) => unwrap(await client.POST('/api/v1/ideas', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ideasKey(userId) }),
  });
}

/** PATCH /ideas/{id}: private ideas by their owner, shared ones by either partner. */
export function useUpdateIdea() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: IdeaPatch }) =>
      unwrap(
        await client.PATCH('/api/v1/ideas/{idea_id}', {
          params: { path: { idea_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ideasKey(userId) }),
  });
}

/** POST /ideas/{id}/share: moves a private idea into the shared deck (cannot be undone). */
export function useShareIdea() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await client.POST('/api/v1/ideas/{idea_id}/share', { params: { path: { idea_id: id } } }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ideasKey(userId) }),
  });
}

/** DELETE /ideas/{id}: archives the idea; nothing is deleted. */
export function useArchiveIdea() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/ideas/{idea_id}', { params: { path: { idea_id: id } } }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ideasKey(userId) }),
  });
}
