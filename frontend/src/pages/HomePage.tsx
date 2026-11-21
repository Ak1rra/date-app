import { ButtonLink } from '../components/Button';
import { SparkleIcon } from '../components/Icons';
import { FinishingTouchesCard } from './home/FinishingTouchesCard';
import { NextDateCard } from './home/NextDateCard';
import { OccasionsCard } from './home/OccasionsCard';

export function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="sr-only">Home</h1>
      <ButtonLink to="/plan" size="xl">
        <SparkleIcon width={28} height={28} />
        Plan a date
      </ButtonLink>

      <div className="grid gap-4 md:grid-cols-2">
        <NextDateCard />
        <FinishingTouchesCard />
        <OccasionsCard />
      </div>
    </div>
  );
}
