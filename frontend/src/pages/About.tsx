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
    body: 'The backend scheduler keeps cycling through bookmaker sources, storing fresh snapshots and exposing progress so the frontend never feels stale or opaque.',
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
    body: 'Every tracked match can be opened to review full fetched odds, player coverage, and discrepancy-linked lines even when the surface board is quiet.',
  },
];

export default function About() {
  return (
    <PageShell
      eyebrow="About the platform"
      title="A disciplined market radar for basketball props."
      description="KvotoLovac turns noisy bookmaker feeds into a readable board of threshold gaps, match coverage, and real-time scan status. It is built for bettors and analysts who care more about fast pattern recognition than sportsbook spectacle."
      aside={
        <div className="space-y-5">
          <div>
            <p className="text-sm text-slate-400">Coverage</p>
            <p className="mt-2 text-3xl font-semibold text-white">8</p>
            <p className="mt-2 text-sm text-slate-400">supported bookmaker integrations in code</p>
          </div>
          <div className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-3 text-sm text-slate-400">
            Current focus: basketball player props, game totals, and discrepancy-first scanning
            across Serbian books.
          </div>
        </div>
      }
    >
      <div className="space-y-6">
        <section className="grid gap-4 lg:grid-cols-2">
          {pillars.map((pillar) => (
            <article
              key={pillar.title}
              className="rounded-xl border border-line-700/70 bg-ink-900 p-6"
            >
              <p className="text-xs text-slate-400">System pillar</p>
              <h3 className="mt-3 text-xl font-semibold text-white">{pillar.title}</h3>
              <p className="mt-3 text-sm leading-7 text-slate-400">{pillar.body}</p>
            </article>
          ))}
        </section>

        <section className="rounded-xl border border-line-700/70 bg-ink-900 p-6">
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
            <div>
              <p className="text-sm text-slate-400">Example opportunity</p>
              <h3 className="mt-3 text-xl font-semibold text-white">Why threshold gaps matter more than raw odds.</h3>
              <p className="mt-3 text-sm leading-7 text-slate-400">
                If one bookmaker lists a player line at Over 18.5 and another shows Under 20.5, a
                narrow scoring band can let both bets win. KvotoLovac surfaces those windows,
                ranks them by profit margin, and links straight into the underlying market tables.
              </p>
            </div>
            <div className="rounded-xl border border-line-700/70 bg-ink-950 p-5">
              <div className="space-y-3 font-mono text-sm text-slate-100">
                <div className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-3">
                  Mozzart → Over 18.5 @ 1.85
                </div>
                <div className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-3">
                  Meridian → Under 20.5 @ 1.90
                </div>
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-200">
                If the player lands on 19 or 20, both tickets cash. That is the core signal the
                dashboard is designed to expose quickly.
              </p>
            </div>
          </div>
        </section>

        <section className="rounded-xl border border-line-700/70 bg-ink-900 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm text-slate-400">Supported bookmakers</p>
              <h3 className="mt-3 text-xl font-semibold text-white">Current integrations</h3>
            </div>
            <Link
              to="/"
              className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-2 text-sm font-medium text-slate-200 transition hover:border-line-500 hover:text-white"
            >
              Back to dashboard
            </Link>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {bookmakers.map((name) => (
              <div
                key={name}
                className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-4"
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
