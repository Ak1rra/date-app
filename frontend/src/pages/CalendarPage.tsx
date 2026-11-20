import { Card, EmptyState } from '../components/EmptyState';
import { CalendarIcon } from '../components/Icons';

export function CalendarPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Calendar</h1>
      <Card>
        <EmptyState icon={<CalendarIcon />} title="Your calendar is empty">
          Planned dates, birthdays and anniversaries appear here so nothing sneaks up on you.
        </EmptyState>
      </Card>
    </div>
  );
}
