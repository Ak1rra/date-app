import { Card, EmptyState } from '../components/EmptyState';
import { UsIcon } from '../components/Icons';

export function UsPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Us</h1>
      <Card>
        <EmptyState icon={<UsIcon />} title="Profiles are on their way">
          Soon you can keep both profiles, important dates and your private notes here.
        </EmptyState>
      </Card>
    </div>
  );
}
