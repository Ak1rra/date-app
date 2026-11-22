import { useState } from 'react';
import { Button } from '../../components/Button';
import { aiError } from '../../api/plannerError';
import { FormError, TextField } from '../../components/forms';
import { useDeck } from '../../hooks/useIdeas';
import type { ItineraryOut } from '../../hooks/useItineraries';
import {
  useAddHint,
  useDeleteHint,
  useHints,
  useRefreshHintsOnChange,
  useRevealNow,
  useSetRevealAt,
  useSuggestHints,
  useUpdateHint,
  type HintOut,
} from '../../hooks/useSurprise';
import { localToIso } from './dateWindow';
import {
  HINT_MAX,
  HintForm,
  isoToLocal,
  kindLabel,
  PAST_TIME_MESSAGE,
  type HintKind,
} from './HintForm';

const when = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const day = new Intl.DateTimeFormat(undefined, { dateStyle: 'full' });

function shareLine(hint: HintOut, startsAt: string | null) {
  const on = startsAt ? ` on ${day.format(new Date(startsAt))}` : '';
  return `Hint for our date${on}: ${hint.text}`;
}

function HintRow({
  hint,
  itinerary,
  canEdit,
  showCopy,
}: {
  hint: HintOut;
  itinerary: ItineraryOut;
  canEdit: boolean;
  showCopy: boolean;
}) {
  const update = useUpdateHint(itinerary.id);
  const remove = useDeleteHint(itinerary.id);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const releasedAt = hint.released_at;

  if (editing && releasedAt === null) {
    return (
      <li className="rounded-2xl border border-line p-3">
        <HintForm
          initial={hint}
          startsAt={itinerary.starts_at}
          submitLabel="Save hint"
          pending={update.isPending}
          error={update.error}
          onCancel={() => setEditing(false)}
          onSubmit={(body) =>
            update.mutate({ hintId: hint.id, body }, { onSuccess: () => setEditing(false) })
          }
        />
      </li>
    );
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareLine(hint, itinerary.starts_at));
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopied(false);
      setCopyFailed(true);
    }
  }

  return (
    <li className="flex flex-col gap-2 rounded-2xl border border-line p-3">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-muted">{kindLabel(hint.kind)}</p>
        <p className="font-semibold">{hint.text}</p>
        <p className="text-sm text-muted">
          {releasedAt !== null
            ? `Released ✓ ${when.format(new Date(releasedAt))}`
            : `Releases ${when.format(new Date(hint.release_at))}`}
        </p>
      </div>
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirm delete">
          <p className="text-sm">Delete this hint?</p>
          <Button
            variant="secondary"
            aria-label={`Yes, delete hint: ${hint.text}`}
            disabled={remove.isPending}
            onClick={() => remove.mutate(hint.id, { onSuccess: () => setConfirming(false) })}
          >
            Yes, delete
          </Button>
          <Button
            variant="secondary"
            disabled={remove.isPending}
            onClick={() => setConfirming(false)}
          >
            Keep it
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {canEdit && releasedAt === null ? (
            <Button
              variant="secondary"
              aria-label={`Edit hint: ${hint.text}`}
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
          ) : null}
          {canEdit ? (
            <Button
              variant="secondary"
              aria-label={`Delete hint: ${hint.text}`}
              onClick={() => setConfirming(true)}
            >
              Delete
            </Button>
          ) : null}
          {showCopy && releasedAt !== null ? (
            <Button
              variant="secondary"
              aria-label={`${copied ? 'Copied' : 'Copy'} hint: ${hint.text}`}
              onClick={() => void copy()}
            >
              {copied ? 'Copied' : 'Copy'}
            </Button>
          ) : null}
        </div>
      )}
      <p role="status" className="text-sm text-muted">
        {copied ? 'Copied ✓' : ''}
      </p>
      {copyFailed ? (
        <p role="alert" className="text-sm text-ink">
          Couldn&apos;t copy — select the text and copy it yourself.
        </p>
      ) : null}
      <FormError error={update.error ?? remove.error} />
    </li>
  );
}

function RevealControlsInner({
  itinerary,
  canEdit,
}: {
  itinerary: ItineraryOut;
  canEdit: boolean;
}) {
  const setRevealAt = useSetRevealAt(itinerary.id);
  const revealNow = useRevealNow(itinerary.id);
  const [time, setTime] = useState(isoToLocal(itinerary.reveal_at ?? itinerary.starts_at));
  const [confirming, setConfirming] = useState(false);
  const [pastError, setPastError] = useState(false);
  const revealedAt = itinerary.revealed_at;
  const target = itinerary.reveal_at ?? itinerary.starts_at;
  const pending = setRevealAt.isPending || revealNow.isPending;

  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <h3 className="text-lg font-semibold">The big reveal</h3>
      <p>
        {revealedAt !== null
          ? `Revealed ✓ ${when.format(new Date(revealedAt))}`
          : target
            ? `Reveals at ${when.format(new Date(target))}`
            : 'Reveals at the start, once you pick a time.'}
      </p>
      {canEdit && revealedAt === null ? (
        <fieldset disabled={pending} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
          <form
            className="flex flex-col gap-3"
            aria-label="Change the reveal time"
            onSubmit={(event) => {
              event.preventDefault();
              if (!time) return;
              if (Date.parse(localToIso(time)) <= Date.now()) {
                setPastError(true);
                return;
              }
              setPastError(false);
              setRevealAt.mutate({ reveal_at: localToIso(time) });
            }}
          >
            <TextField
              label="Reveal time"
              type="datetime-local"
              value={time}
              onChange={(event) => {
                setTime(event.target.value);
                setPastError(false);
              }}
            />
            {pastError ? (
              <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
                {PAST_TIME_MESSAGE}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" variant="secondary" disabled={!time || pending}>
                Set reveal time
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={pending}
                onClick={() =>
                  setRevealAt.mutate(
                    { reveal_at: null },
                    { onSuccess: (data) => setTime(isoToLocal(data.reveal_at ?? data.starts_at)) },
                  )
                }
              >
                At the start
              </Button>
            </div>
          </form>
          {itinerary.status === 'scheduled' ? (
            confirming ? (
              <div
                className="flex flex-wrap items-center gap-2"
                role="group"
                aria-label="Confirm reveal"
              >
                <p className="text-sm">Reveal it now? They&apos;ll get a notification.</p>
                <Button
                  disabled={pending}
                  onClick={() =>
                    revealNow.mutate(undefined, { onSuccess: () => setConfirming(false) })
                  }
                >
                  Yes, reveal it
                </Button>
                <Button variant="secondary" disabled={pending} onClick={() => setConfirming(false)}>
                  Not yet
                </Button>
              </div>
            ) : (
              <div>
                <Button disabled={pending} onClick={() => setConfirming(true)}>
                  Reveal now
                </Button>
              </div>
            )
          ) : null}
        </fieldset>
      ) : null}
      <FormError error={setRevealAt.error ?? revealNow.error} />
    </div>
  );
}

/** Remounts on a server-side change so the reveal-time input never shows a stale value. */
function RevealControls({ itinerary, canEdit }: { itinerary: ItineraryOut; canEdit: boolean }) {
  return (
    <RevealControlsInner
      key={`${itinerary.id}|${itinerary.reveal_at}|${itinerary.starts_at}`}
      itinerary={itinerary}
      canEdit={canEdit}
    />
  );
}

/** The planner's side of a surprise: hints that trickle out, and when the big reveal happens. */
export function SurpriseSection({ itinerary }: { itinerary: ItineraryOut }) {
  // The hints route refuses a completed or cancelled date, so only ask while it can change.
  const canEdit = itinerary.status === 'draft' || itinerary.status === 'scheduled';
  const hints = useHints(itinerary.id, canEdit);
  useRefreshHintsOnChange(itinerary.id, `${itinerary.updated_at}|${itinerary.status}`);
  const add = useAddHint(itinerary.id);
  const deck = useDeck();
  const suggest = useSuggestHints(itinerary.id);
  const [formKey, setFormKey] = useState(0);
  const [prefill, setPrefill] = useState<{ kind: HintKind; text: string } | undefined>();
  const list = hints.data ?? [];
  const full = list.length >= HINT_MAX;
  // Nobody to notify until the partner has joined, so the planner shares released hints by hand.
  const unpaired = deck.data?.partner_joined === false;

  return (
    <section
      aria-labelledby="surprise-heading"
      className="flex flex-col gap-4 rounded-3xl border border-line bg-surface p-4 shadow-sm"
    >
      <div>
        <h2 id="surprise-heading" className="text-xl font-semibold">
          Surprise ✨
        </h2>
        <p className="text-sm text-muted">
          Drop little hints, then pick the moment the whole plan appears.
        </p>
      </div>

      {hints.isPending ? (
        <p role="status" className="text-muted">
          Loading your hints…
        </p>
      ) : hints.isError ? (
        <FormError error={hints.error} />
      ) : list.length > 0 ? (
        <ul className="flex flex-col gap-2" aria-label="Hints">
          {list.map((hint) => (
            <HintRow
              key={hint.id}
              hint={hint}
              itinerary={itinerary}
              canEdit={canEdit}
              showCopy={unpaired}
            />
          ))}
        </ul>
      ) : (
        <p className="text-muted">
          No hints yet. A tiny clue or two makes the waiting part of the fun.
        </p>
      )}

      {canEdit && hints.isSuccess ? (
        full ? (
          <p className="text-sm text-muted">That&apos;s 20 hints, the most a surprise can hold.</p>
        ) : (
          <div className="flex flex-col gap-3 border-t border-line pt-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-lg font-semibold">Add a hint</h3>
              <Button
                type="button"
                variant="secondary"
                disabled={suggest.isPending}
                onClick={() => suggest.mutate()}
              >
                {suggest.isPending ? 'Thinking of hints…' : 'Suggest hints ✨'}
              </Button>
            </div>
            <FormError error={aiError(suggest.error)} />
            {suggest.data ? (
              suggest.data.suggestions.length > 0 ? (
                <ul className="flex flex-col gap-2" aria-label="Suggested hints">
                  {suggest.data.suggestions.map((suggestion, index) => (
                    <li
                      key={`${index}|${suggestion.kind}|${suggestion.text}`}
                      className="flex flex-col gap-2 rounded-2xl border border-line bg-surface-2 p-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-muted">
                          {kindLabel(suggestion.kind)}
                        </p>
                        <p className="font-semibold">{suggestion.text}</p>
                      </div>
                      <div>
                        <Button
                          type="button"
                          variant="secondary"
                          aria-label={`Use this hint: ${suggestion.text}`}
                          // A pending add would clear this prefill on success and wipe the new pick.
                          disabled={add.isPending}
                          onClick={() => {
                            setPrefill({ kind: suggestion.kind, text: suggestion.text });
                            setFormKey((value) => value + 1);
                          }}
                        >
                          Use this
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted">No suggestions this time. Try again in a bit.</p>
              )
            ) : null}
            <HintForm
              key={formKey}
              prefill={prefill}
              startsAt={itinerary.starts_at}
              submitLabel="Add hint"
              pending={add.isPending}
              error={add.error}
              onSubmit={(body) =>
                add.mutate(body, {
                  onSuccess: () => {
                    setPrefill(undefined);
                    setFormKey((value) => value + 1);
                  },
                })
              }
            />
          </div>
        )
      ) : null}

      <RevealControls itinerary={itinerary} canEdit={canEdit} />
    </section>
  );
}
