import { useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/EmptyState';
import { useDeck, useIdeaLists, type IdeaOut } from '../hooks/useIdeas';
import { useViewerId } from '../hooks/useSession';
import { useMatchCelebration } from '../realtime/RealtimeProvider';
import { DeckTab } from './ideas/DeckTab';
import { GenerateIdeasSheet, type GenerateMode } from './ideas/GenerateIdeasSheet';
import { IdeaLists, type ListName } from './ideas/IdeaLists';
import { MyIdeasTab } from './ideas/MyIdeasTab';
import { usePeople } from './ideas/usePeople';

type TabId = 'deck' | ListName | 'mine';

/**
 * Keyed by the viewer, so a profile switch remounts the body: the match overlay, the voted-card
 * set and the tab all reset, and a vote still in flight for the previous profile can no longer
 * call back into this page.
 */
export function IdeasPage() {
  const viewerId = useViewerId();
  return <IdeasBody key={viewerId ?? 'none'} />;
}

function IdeasBody() {
  const people = usePeople();
  const lists = useIdeaLists().data;
  const viewerId = useViewerId();
  const deck = useDeck().data;
  const [tab, setTab] = useState<TabId>('deck');
  // Owned here, outside the tab panel and its loading/error branches, so a deck refetch that
  // fails can't unmount the sheet and lose its success or degraded notice.
  const [generateMode, setGenerateMode] = useState<GenerateMode | null>(null);
  // The overlay lives in RealtimeProvider so a match found anywhere raises the same one.
  const celebration = useMatchCelebration();
  const onMatch = (idea: IdeaOut) => celebration.celebrate(idea.id, idea.title);

  const count = (name: ListName) => (lists ? ` (${lists[name].length})` : '');
  const tabs: { id: TabId; label: string }[] = [
    { id: 'deck', label: 'Deck' },
    { id: 'matches', label: `Matches${count('matches')}` },
    { id: 'maybes', label: `Maybes${count('maybes')}` },
    { id: 'pending_partner', label: `Waiting on ${people.partnerName}${count('pending_partner')}` },
    { id: 'passed', label: `Passed${count('passed')}` },
    { id: 'mine', label: 'My ideas' },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Ideas</h1>
        {viewerId ? (
          <Button
            variant="secondary"
            disabled={!deck}
            onClick={() => deck && setGenerateMode(deck.partner_joined ? 'match' : 'plan')}
          >
            Generate ideas
          </Button>
        ) : null}
      </div>
      <div
        role="tablist"
        aria-label="Idea lists"
        className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1"
      >
        {tabs.map((t) => {
          const selected = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`ideas-tab-${t.id}`}
              aria-selected={selected}
              aria-controls="ideas-panel"
              onClick={() => setTab(t.id)}
              className={`min-h-11 shrink-0 rounded-full border px-4 py-2 text-sm font-semibold whitespace-nowrap motion-safe:transition ${
                selected
                  ? 'border-2 border-accent bg-accent-soft text-accent'
                  : 'border-line bg-surface text-ink hover:bg-surface-2'
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <Card>
        <div role="tabpanel" id="ideas-panel" aria-labelledby={`ideas-tab-${tab}`}>
          {tab === 'deck' ? (
            <DeckTab
              partnerName={people.partnerName}
              paused={celebration.active || generateMode !== null}
              onMatch={onMatch}
              onGenerate={() => setGenerateMode('match')}
            />
          ) : tab === 'mine' ? (
            <MyIdeasTab partnerName={people.partnerName} />
          ) : (
            <IdeaLists which={tab} people={people} onMatch={onMatch} />
          )}
        </div>
      </Card>
      {generateMode ? (
        <GenerateIdeasSheet defaultMode={generateMode} onClose={() => setGenerateMode(null)} />
      ) : null}
    </div>
  );
}
