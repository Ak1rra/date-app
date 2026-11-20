import { Button } from '../components/Button';
import { Card, EmptyState } from '../components/EmptyState';
import { IdeasIcon, UsIcon } from '../components/Icons';

export function IdeasPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Ideas</h1>
      <Card title="Swipe deck">
        <EmptyState
          icon={<UsIcon />}
          title="Swipe on ideas together"
          action={<Button variant="secondary">Invite your partner to swipe together</Button>}
        >
          Once your partner joins, you&apos;ll each swipe on date ideas and see where you match.
        </EmptyState>
      </Card>
      <Card title="My ideas">
        <EmptyState icon={<IdeasIcon />} title="No ideas saved yet">
          Jot down places, activities and little things you notice. They stay private to you until
          you choose to share them.
        </EmptyState>
      </Card>
    </div>
  );
}
