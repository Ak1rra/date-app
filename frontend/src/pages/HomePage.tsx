import { useState } from 'react';
import { ButtonLink } from '../components/Button';
import { Card } from '../components/EmptyState';
import { SparkleIcon } from '../components/Icons';
import { getPendingInvite, useEmailMode } from '../hooks/usePairing';
import { FinishingTouchesCard } from './home/FinishingTouchesCard';
import { NextDateCard } from './home/NextDateCard';
import { OccasionsCard } from './home/OccasionsCard';
import { SurprisesCard } from './home/SurprisesCard';

export function HomePage() {
  const emailMode = useEmailMode();
  // Read once: an invite opened in this visit shouldn't make the card appear and vanish.
  const [hasPendingInvite] = useState(() => getPendingInvite() !== null);
  return (
    <div className="flex flex-col gap-6">
      <h1 className="sr-only">Home</h1>
      {emailMode && hasPendingInvite ? (
        <Card title="Invite waiting">
          <div className="flex flex-col gap-3 p-2">
            <ButtonLink to="/join" variant="secondary">
              Finish joining your partner
            </ButtonLink>
          </div>
        </Card>
      ) : null}
      <ButtonLink to="/plan" size="xl">
        <SparkleIcon width={28} height={28} />
        Plan a date
      </ButtonLink>

      <div className="grid gap-4 md:grid-cols-2">
        <SurprisesCard />
        <NextDateCard />
        <FinishingTouchesCard />
        <OccasionsCard />
      </div>
    </div>
  );
}
