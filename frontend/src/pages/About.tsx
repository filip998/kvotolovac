import { Link } from 'react-router-dom';

export default function About() {
  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <div className="mb-4 text-center text-5xl">🎯</div>
        <h2 className="text-center text-2xl font-bold text-white">KvotoLovac</h2>
        <p className="mt-1 text-center text-sm text-brand-400">Odds Hunter</p>
      </div>

      <div className="space-y-4 text-sm leading-relaxed text-gray-400">
        <p>
          <strong className="text-white">KvotoLovac</strong> (Odds Hunter) is an odds comparison
          tool designed for Serbian bookmakers. It monitors basketball betting lines across
          multiple bookmakers and detects discrepancies — situations where different bookmakers
          offer odds on the same market with different thresholds.
        </p>

        <h3 className="pt-4 text-lg font-semibold text-white">How It Works</h3>
        <ol className="list-inside list-decimal space-y-2">
          <li>
            <strong className="text-gray-200">Scraping:</strong> The system periodically scrapes
            odds from Serbian bookmakers (Mozzart, Meridian, MaxBet).
          </li>
          <li>
            <strong className="text-gray-200">Comparison:</strong> For each market (player
            points, game totals), it compares thresholds and odds across bookmakers.
          </li>
          <li>
            <strong className="text-gray-200">Detection:</strong> When a threshold gap is found
            (e.g., one bookmaker offers Over 18.5 while another offers Under 20.5), the system
            calculates the profit margin.
          </li>
          <li>
            <strong className="text-gray-200">Alerting:</strong> Discrepancies are ranked by
            profit margin and displayed on the dashboard.
          </li>
        </ol>

        <h3 className="pt-4 text-lg font-semibold text-white">Understanding Discrepancies</h3>
        <p>
          A <strong className="text-brand-400">discrepancy</strong> occurs when two bookmakers
          set different thresholds for the same player/market. For example:
        </p>
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-4 font-mono text-xs">
          <div className="text-gray-300">Mozzart: Vezenkov Points Over 18.5 @ 1.85</div>
          <div className="text-gray-300">Meridian: Vezenkov Points Under 20.5 @ 1.90</div>
          <div className="mt-2 text-brand-400">
            → Gap: 2.0 pts | Both bets win if Vezenkov scores 19 or 20 points
          </div>
        </div>

        <h3 className="pt-4 text-lg font-semibold text-white">Supported Bookmakers</h3>
        <div className="grid grid-cols-3 gap-3">
          {['Mozzart', 'Meridian', 'MaxBet'].map((name) => (
            <div
              key={name}
              className="rounded-lg border border-gray-800 bg-gray-900/50 p-3 text-center font-medium text-gray-200"
            >
              {name}
            </div>
          ))}
        </div>
      </div>

      <div className="text-center">
        <Link
          to="/"
          className="inline-block rounded-lg bg-brand-600 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-500"
        >
          Go to Dashboard →
        </Link>
      </div>
    </div>
  );
}
