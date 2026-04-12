import { Link } from 'react-router-dom';
import BookmakerBadge from '../components/BookmakerBadge';
import PageShell from '../components/PageShell';

const bookmakers = [
  'Mozzart',
  'Meridian',
  'MaxBet',
  'OktagonBet',
  'AdmiralBet',
  'BalkanBet',
  'MerkurXTip',
  'PinnBet',
];

const pillars = [
  {
    title: 'Continuous monitoring',
    body: 'The backend scheduler keeps cycling through bookmaker sources, storing fresh snapshots and exposing progress so the frontend never feels stale.',
  },
  {
    title: 'Smart normalization',
    body: 'Teams, players, leagues, and market types are normalized before analysis so cross-bookmaker comparisons stay trustworthy.',
  },
  {
    title: 'Discrepancy detection',
    body: 'Threshold gaps are ranked by profit margin so the highest-value middles float to the top of the board immediately.',
  },
  {
    title: 'Drill-down inspection',
    body: 'Every tracked match can be opened to review full fetched odds, player coverage, and discrepancy-linked lines.',
  },
];

export default function About() {
  return (
    <PageShell
      eyebrow="About"
      title="A disciplined market radar for basketball props."
      description="KvotoLovac turns noisy bookmaker feeds into a readable board of threshold gaps, match coverage, and real-time scan status."
    >
      <div className="space-y-6">
        <section className="grid gap-3 lg:grid-cols-2">
          {pillars.map((pillar) => (
            <article
              key={pillar.title}
              className="rounded-lg border border-border bg-surface p-5"
            >
              <h3 className="text-sm font-semibold text-text">{pillar.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-text-secondary">{pillar.body}</p>
            </article>
          ))}
        </section>

        <section className="rounded-lg border border-border bg-surface p-5">
          <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
            <div>
              <h3 className="text-sm font-semibold text-text">Why threshold gaps matter more than raw odds</h3>
              <p className="mt-2 text-sm leading-relaxed text-text-secondary">
                If one bookmaker lists a player line at Over 18.5 and another shows Under 20.5, a
                narrow scoring band can let both bets win. KvotoLovac surfaces those windows,
                ranks them by profit margin, and links straight into the underlying market tables.
              </p>
            </div>
            <div className="space-y-2">
              <div className="rounded-md bg-surface-raised px-4 py-3 font-mono text-sm text-text">
                Mozzart → Over 18.5 @ 1.85
              </div>
              <div className="rounded-md bg-surface-raised px-4 py-3 font-mono text-sm text-text">
                Meridian → Under 20.5 @ 1.90
              </div>
              <p className="mt-3 text-sm text-text-secondary">
                If the player lands on 19 or 20, both tickets cash.
              </p>
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-border bg-surface p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-text">Supported bookmakers</h3>
            <Link
              to="/"
              className="text-xs font-medium text-text-muted transition hover:text-accent"
            >
              Back to dashboard →
            </Link>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {bookmakers.map((name) => (
              <div
                key={name}
                className="rounded-md bg-surface-raised px-4 py-3"
              >
                <BookmakerBadge name={name} />
              </div>
            ))}
          </div>
        </section>
      </div>
    </PageShell>
  );
}
