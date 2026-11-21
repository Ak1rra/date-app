import { useState } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { FormError, LoadError, PrivateBadge, Skeleton } from '../../components/forms';
import { IdeasIcon } from '../../components/Icons';
import {
  useArchiveIdea,
  useCreateIdea,
  useMyIdeas,
  useShareIdea,
  useUpdateIdea,
  type IdeaOut,
} from '../../hooks/useIdeas';
import { IdeaDetails } from './IdeaDetails';
import { IdeaForm } from './IdeaForm';
import { MakeItADateButton } from './MakeItADateSheet';

function MyIdeaRow({ idea, partnerName }: { idea: IdeaOut; partnerName: string }) {
  const [editing, setEditing] = useState(false);
  const [confirmingShare, setConfirmingShare] = useState(false);
  const update = useUpdateIdea();
  const share = useShareIdea();
  const archive = useArchiveIdea();

  if (editing) {
    return (
      <li className="rounded-2xl border border-line p-3">
        <IdeaForm
          idea={idea}
          submitLabel="Save changes"
          pending={update.isPending}
          error={update.error}
          onSubmit={(body) =>
            update.mutate({ id: idea.id, body }, { onSuccess: () => setEditing(false) })
          }
          onCancel={() => {
            update.reset();
            setEditing(false);
          }}
        />
      </li>
    );
  }

  return (
    <li className="flex flex-col gap-3 rounded-2xl border border-line p-3">
      <IdeaDetails idea={idea} titleLevel={4} />
      {confirmingShare ? (
        <div role="group" aria-label={`Share ${idea.title}?`} className="flex flex-col gap-2">
          <p>
            Share this with {partnerName}? They&apos;ll see it and can swipe on it. Once it&apos;s
            in your shared deck, it can&apos;t go back to private.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={share.isPending}
              onClick={() => share.mutate(idea.id, { onSuccess: () => setConfirmingShare(false) })}
            >
              Yes, share it
            </Button>
            <Button variant="secondary" onClick={() => setConfirmingShare(false)}>
              Not yet
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            aria-label={`Edit ${idea.title}`}
            onClick={() => setEditing(true)}
          >
            Edit
          </Button>
          <Button
            variant="secondary"
            aria-label={`Share ${idea.title}`}
            onClick={() => {
              share.reset();
              setConfirmingShare(true);
            }}
          >
            Share
          </Button>
          <MakeItADateButton idea={idea} />
          <Button
            variant="secondary"
            aria-label={`Archive ${idea.title}`}
            disabled={archive.isPending}
            onClick={() => archive.mutate(idea.id)}
          >
            Archive
          </Button>
        </div>
      )}
      <FormError error={share.error ?? archive.error} />
    </li>
  );
}

/** My private ideas: jot them down, polish them, then share the good ones with my partner. */
export function MyIdeasTab({ partnerName }: { partnerName: string }) {
  const ideas = useMyIdeas();
  const create = useCreateIdea();
  // Bumping the key resets the form after a save.
  const [formKey, setFormKey] = useState(0);

  return (
    <div className="flex flex-col gap-4 pt-2">
      <div className="px-2">
        <PrivateBadge />
      </div>
      <IdeaForm
        key={formKey}
        submitLabel="Save idea"
        pending={create.isPending}
        error={create.error}
        onSubmit={(body) => create.mutate(body, { onSuccess: () => setFormKey((k) => k + 1) })}
      />
      {ideas.isPending ? <Skeleton label="Loading your ideas" /> : null}
      {ideas.isError ? (
        <LoadError error={ideas.error} onRetry={() => void ideas.refetch()} />
      ) : null}
      {ideas.data && ideas.data.length === 0 ? (
        <EmptyState icon={<IdeasIcon />} title="No ideas saved yet">
          Jot down places, activities and little things you notice. They stay private to you until
          you choose to share them.
        </EmptyState>
      ) : null}
      {ideas.data && ideas.data.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {ideas.data.map((idea) => (
            <MyIdeaRow key={idea.id} idea={idea} partnerName={partnerName} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}
