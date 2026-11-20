import { ButtonLink } from '../components/Button';
import { Card, EmptyState } from '../components/EmptyState';
import { CalendarIcon, SparkleIcon } from '../components/Icons';

export function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="sr-only">Home</h1>
      <ButtonLink to="/plan" size="xl">
        <SparkleIcon width={28} height={28} />
        Plan a date
      </ButtonLink>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Next date" className="md:col-span-2">
          <EmptyState title="Nothing planned yet">
            Your next date will show up here, with the countdown and everything you still need to
            do. Tap &ldquo;Plan a date&rdquo; to start one.
          </EmptyState>
        </Card>
        <Card title="Finishing touches due">
          <EmptyState title="All caught up">
            When a planned date needs a reservation, a gift or a booking, the reminders land here.
          </EmptyState>
        </Card>
        <Card title="Upcoming occasions">
          <EmptyState title="No occasions yet" icon={<CalendarIcon />}>
            Add birthdays and anniversaries in Us and we&apos;ll help you plan ahead.
          </EmptyState>
        </Card>
      </div>
    </div>
  );
}
