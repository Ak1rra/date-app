import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { useViewerId } from './useSession';

export type NoteOut = Schemas['NoteOut'];
export type NoteIn = Schemas['NoteIn'];
export type NotePatch = Schemas['NotePatch'];
export type NoteKind = NoteIn['kind'];

const notesKey = (userId: string | undefined) => ['notes', userId] as const;

/** GET /notes?kind=: only your own, non-archived notes (the API never returns anyone else's). */
export function useNotes(kind: NoteKind) {
  const userId = useViewerId();
  return useQuery({
    queryKey: [...notesKey(userId), kind],
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/notes', { params: { query: { kind } } })),
  });
}

/** POST /notes */
export function useCreateNote() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: NoteIn) => unwrap(await client.POST('/api/v1/notes', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notesKey(userId) }),
  });
}

/** PATCH /notes/{id}: edit the text or archive. */
export function useUpdateNote() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: NotePatch }) =>
      unwrap(
        await client.PATCH('/api/v1/notes/{note_id}', {
          params: { path: { note_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notesKey(userId) }),
  });
}

/** DELETE /notes/{id} */
export function useDeleteNote() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/notes/{note_id}', { params: { path: { note_id: id } } }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notesKey(userId) }),
  });
}
