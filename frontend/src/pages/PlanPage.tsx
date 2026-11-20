import { ButtonLink } from '../components/Button';
import { Card, EmptyState } from '../components/EmptyState';
import { SparkleIcon } from '../components/Icons';

export function PlanPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Plan a date</h1>
      <Card>
        <EmptyState
          icon={<SparkleIcon />}
          title="Plan mode arrives in the next milestone"
          action={
            <ButtonLink to="/" variant="secondary">
              Back home
            </ButtonLink>
          }
        >
          Soon you&apos;ll be able to pick a vibe and get a full plan here.
        </EmptyState>
      </Card>
    </div>
  );
}
