import { Button } from '../../components/Button';
import { TextAreaField } from '../../components/forms';
import { CHAT_MAX_LENGTH } from '../../hooks/useChat';

const COUNTER_FROM = Math.floor(CHAT_MAX_LENGTH * 0.9);

export const SUGGESTIONS = [
  'Plan a cozy night in',
  'Find something fun this weekend',
  'Surprise idea under $50',
];

/**
 * The message box. Enter sends, Shift+Enter adds a line. `draft` / `onDraft` are owned by the
 * page so a suggestion chip can fill it.
 */
export function Composer({
  draft,
  onDraft,
  onSend,
  onStop,
  streaming,
  ready,
  error,
}: {
  draft: string;
  onDraft: (next: string) => void;
  /** Returns whether the message was taken; the draft is only cleared when it was. */
  onSend: (content: string) => boolean;
  onStop: () => void;
  streaming: boolean;
  /** False until the conversation has loaded, so a send cannot land before its history. */
  ready: boolean;
  error: string | null;
}) {
  const trimmed = draft.trim();
  const canSend = ready && !streaming && trimmed.length > 0 && draft.length <= CHAT_MAX_LENGTH;

  const submit = () => {
    if (!canSend) return;
    if (onSend(trimmed)) onDraft('');
  };

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <TextAreaField
        label="Message"
        rows={2}
        value={draft}
        maxLength={CHAT_MAX_LENGTH}
        placeholder="Ask for a date idea, a plan, a time…"
        onChange={(e) => onDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        hint="Enter sends, Shift+Enter adds a new line."
      />
      {draft.length >= COUNTER_FROM ? (
        <p className="text-right text-sm text-muted" aria-live="polite">
          {draft.length} / {CHAT_MAX_LENGTH}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
          <strong className="font-semibold">Couldn&apos;t send. </strong>
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        {streaming ? (
          <Button variant="secondary" onClick={onStop}>
            Stop
          </Button>
        ) : null}
        <Button type="submit" disabled={!canSend}>
          Send
        </Button>
      </div>
    </form>
  );
}
