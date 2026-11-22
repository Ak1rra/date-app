import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, ButtonLink } from '../components/Button';
import { Card, EmptyState } from '../components/EmptyState';
import { FormError, LoadError, Skeleton, TextField } from '../components/forms';
import {
  useDeleteTemplate,
  useReplanTemplate,
  useTemplates,
  type TemplateOut,
} from '../hooks/useTemplates';
import { plannerError } from '../api/plannerError';
import { localToIso } from './itinerary/dateWindow';
import { Sheet } from '../components/Sheet';

function ReplanSheet({ template, onClose }: { template: TemplateOut; onClose: () => void }) {
  const navigate = useNavigate();
  const replan = useReplanTemplate();
  const [starts, setStarts] = useState('');
  const [ends, setEnds] = useState('');
  const endsTooEarly = !!starts && !!ends && new Date(ends) <= new Date(starts);

  return (
    <Sheet
      labelledBy="replan-title"
      className="w-full max-w-lg rounded-3xl bg-surface p-5 shadow-xl"
      onClose={onClose}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 id="replan-title" className="text-xl font-semibold">
            Plan it again
          </h2>
          <p className="mt-1 text-sm text-muted">
            A fresh take on &ldquo;{template.name}&rdquo;, at a time that suits you.
          </p>
        </div>
        <Button variant="secondary" onClick={onClose} aria-label="Close replan">
          Close
        </Button>
      </div>
      <form
        className="mt-5 flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!starts || endsTooEarly) return;
          replan.mutate(
            {
              id: template.id,
              body: { starts_at: localToIso(starts), ends_at: ends ? localToIso(ends) : null },
            },
            { onSuccess: (itinerary) => navigate(`/itineraries/${itinerary.id}`) },
          );
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label="Starts"
            type="datetime-local"
            required
            value={starts}
            onChange={(event) => setStarts(event.target.value)}
          />
          <TextField
            label="Ends (optional)"
            type="datetime-local"
            value={ends}
            onChange={(event) => setEnds(event.target.value)}
            hint={endsTooEarly ? 'The end needs to be after the start.' : undefined}
            aria-invalid={endsTooEarly || undefined}
          />
        </div>
        <FormError error={plannerError(replan.error)} />
        <Button type="submit" disabled={!starts || endsTooEarly || replan.isPending}>
          {replan.isPending ? 'Planning…' : 'Plan this date'}
        </Button>
      </form>
    </Sheet>
  );
}

function TemplateRow({ template }: { template: TemplateOut }) {
  const [replanning, setReplanning] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const remove = useDeleteTemplate();

  return (
    <li className="flex flex-col gap-3 rounded-2xl border border-line p-3">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">{template.name}</h2>
        <p className="text-sm text-muted">{template.title}</p>
        {template.stop_titles.length > 0 ? (
          <ol className="list-inside list-decimal text-sm">
            {template.stop_titles.map((stop, index) => (
              <li key={`${stop}-${index}`}>{stop}</li>
            ))}
          </ol>
        ) : null}
      </div>
      {confirmingDelete ? (
        <div role="group" aria-label={`Delete ${template.name}?`} className="flex flex-col gap-2">
          <p>Delete this template? Dates you already planned from it stay put.</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={remove.isPending} onClick={() => remove.mutate(template.id)}>
              {remove.isPending ? 'Deleting…' : 'Yes, delete it'}
            </Button>
            <Button variant="secondary" onClick={() => setConfirmingDelete(false)}>
              Keep it
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button aria-label={`Replan ${template.name}`} onClick={() => setReplanning(true)}>
            Replan
          </Button>
          <Button
            variant="secondary"
            aria-label={`Delete ${template.name}`}
            onClick={() => {
              remove.reset();
              setConfirmingDelete(true);
            }}
          >
            Delete
          </Button>
        </div>
      )}
      <FormError error={remove.error} />
      {replanning ? <ReplanSheet template={template} onClose={() => setReplanning(false)} /> : null}
    </li>
  );
}

export function TemplatesPage() {
  const templates = useTemplates();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-muted">Dates worth repeating</p>
          <h1 className="text-2xl font-bold">Templates</h1>
        </div>
        <ButtonLink to="/itineraries" variant="secondary">
          Itineraries
        </ButtonLink>
      </div>

      <Card>
        {templates.isPending ? <Skeleton label="Loading templates" /> : null}
        {templates.isError ? (
          <LoadError error={templates.error} onRetry={() => void templates.refetch()} />
        ) : null}
        {templates.data?.length === 0 ? (
          <EmptyState title="No templates yet">
            Open one of your dates and choose Save as template to keep its shape for next time.
          </EmptyState>
        ) : null}
        {templates.data && templates.data.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {templates.data.map((template) => (
              <TemplateRow key={template.id} template={template} />
            ))}
          </ul>
        ) : null}
      </Card>
    </div>
  );
}
