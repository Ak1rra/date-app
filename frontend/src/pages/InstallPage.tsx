import { Card } from '../components/EmptyState';
import { isStandalone } from '../lib/install';

const steps = [
  {
    title: 'Open Date Planner in Safari',
    body: (
      <p>
        Start at <strong className="font-semibold">https://dates.lan</strong> in Safari. It has to
        be Safari, because other browsers can&apos;t add apps to your Home Screen. Don&apos;t be
        shy, it won&apos;t bite.
      </p>
    ),
  },
  {
    title: 'Trust the certificate',
    body: (
      <>
        <p>
          Our house runs on its own little certificate authority, so your phone needs to be
          introduced first. Ask the household admin for the{' '}
          <strong className="font-semibold">root.crt</strong> file (it is copied from the Caddy
          box).
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>AirDrop or email root.crt to your iPhone, then tap it.</li>
          <li>Go to Settings → General → VPN &amp; Device Management and install the profile.</li>
          <li>
            Then go to Settings → General → About → Certificate Trust Settings and turn on full
            trust for the Caddy root.
          </li>
        </ul>
      </>
    ),
  },
  {
    title: 'Add it to your Home Screen',
    body: (
      <p>
        Tap the Share button in Safari, scroll down, and choose{' '}
        <strong className="font-semibold">Add to Home Screen</strong>. Now we&apos;re an icon you
        can reach in one tap.
      </p>
    ),
  },
  {
    title: 'Open it from the Home Screen',
    body: (
      <p>
        Launch Date Planner from the icon you just added, not from Safari. On iPhone (iOS 16.4 or
        newer) this is required for notifications, so skip it and we can&apos;t sweet-talk you with
        reminders.
      </p>
    ),
  },
];

export function InstallPage() {
  const installed = isStandalone();

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Get Date Planner on your iPhone</h1>
      {installed ? (
        <Card>
          <h2 className="p-2 text-xl font-semibold">You&apos;re all set 💘</h2>
          <p className="px-2 pb-2 text-muted">
            Date Planner is already on your Home Screen. Nothing left to do but plan something
            delicious.
          </p>
        </Card>
      ) : (
        <p className="text-muted">
          Four quick steps and I&apos;ll live right next to your other favourite apps. Follow them
          in order, and I&apos;ll make it worth your while. 😉
        </p>
      )}
      <ol className="flex flex-col gap-4" aria-label="Install steps">
        {steps.map((step, index) => (
          <li key={step.title}>
            <Card>
              <div className="flex flex-col gap-2 p-2">
                <h2 className="text-lg font-semibold">
                  Step {index + 1}: {step.title}
                </h2>
                <div className="text-muted">{step.body}</div>
              </div>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}
