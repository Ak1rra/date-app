import { Link } from 'react-router';
import { Card } from '../components/EmptyState';

const sections = [
  {
    title: 'OpenAI',
    body: (
      <>
        <p>
          When you use the AI features (idea generation, planning, chat, hints and taste summaries),
          the relevant profile details, notes, ideas and messages are sent to OpenAI&apos;s models
          so they can write a reply. Surprise details are never sent in a way the person being
          surprised can see. OpenAI says API data isn&apos;t used to train its models unless an
          organization opts in. The app asks OpenAI not to store its requests, but OpenAI may keep
          abuse-monitoring logs for up to 30 days.
        </p>
        <p className="mt-2">
          Read more in{' '}
          <a
            className="font-semibold underline"
            href="https://openai.com/enterprise-privacy/"
            target="_blank"
            rel="noreferrer"
          >
            OpenAI&apos;s API data privacy page
          </a>
          .
        </p>
      </>
    ),
  },
  {
    title: 'Google Maps Platform',
    body: (
      <p>
        Used for place searches and venue lookups. Google receives your search text, plus the
        approximate coordinates of your home area so results can be nearby.
      </p>
    ),
  },
  {
    title: 'Ticketmaster',
    body: <p>Used to find events. Ticketmaster receives your area and the date range you want.</p>,
  },
  {
    title: 'Open-Meteo',
    body: (
      <p>
        Used for weather forecasts. Open-Meteo receives coordinates only. There&apos;s no key and no
        account involved.
      </p>
    ),
  },
  {
    title: 'Resend',
    body: (
      <p>
        Used to deliver email, and only if email sign-in is turned on. It&apos;s off in this
        household setup, so nothing goes to Resend.
      </p>
    ),
  },
  {
    title: 'Web Push services',
    body: (
      <p>
        If you turn on notifications, Apple, Google or Mozilla (depending on your device) carries
        them to your phone. They see only a notification&apos;s title and short text.
      </p>
    ),
  },
  {
    title: 'Everything else stays on your own server',
    body: (
      <p>
        Profiles, notes, votes, ratings and chat history live on the server in your home, not with
        any of the services above.
      </p>
    ),
  },
  {
    title: 'No login means trust',
    body: (
      <p>
        There&apos;s no password. Anyone who can open the app on your home network can switch
        between the Husband and Wife profiles, so keeping a surprise secret relies on trust, not on
        locks.
      </p>
    ),
  },
];

export function PrivacyPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col gap-6 bg-canvas px-4 py-6 text-ink md:px-6">
      <Link to="/profile" className="font-semibold underline">
        ← Back
      </Link>
      <h1 className="text-2xl font-bold">Privacy</h1>
      <p className="text-muted">
        Date Planner runs on your own server. Here&apos;s who else ever hears from it, what they
        get, and why.
      </p>
      {sections.map((section) => (
        <Card key={section.title}>
          <div className="flex flex-col gap-2 p-2">
            <h2 className="text-lg font-semibold">{section.title}</h2>
            <div className="text-muted">{section.body}</div>
          </div>
        </Card>
      ))}
    </main>
  );
}
