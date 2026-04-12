import { useParams, Link } from 'react-router-dom';
import { useMatch, useMatchOdds, useDiscrepancies } from '../api/hooks';
import { formatDateTime } from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import OddsTable from '../components/OddsTable';
import LoadingSpinner from '../components/LoadingSpinner';
import PageShell from '../components/PageShell';
import type { OddsOffer } from '../api/types';

interface MarketGroup {
  key: string;
  title: string;
  offers: OddsOffer[];
}

export default function MatchDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: match, isLoading: matchLoading } = useMatch(id!);
  const { data: odds, isLoading: oddsLoading } = useMatchOdds(id!);
  const { data: discrepancies } = useDiscrepancies();

  if (matchLoading || oddsLoading) return <LoadingSpinner />;

  if (!match) {
    return (
      <div className="py-16 text-center">
        <div className="mb-4 text-5xl">🔍</div>
        <h2 className="mb-2 text-lg font-semibold text-slate-300">Match not found</h2>
        <Link to="/" className="text-sm text-slate-300 hover:text-white">
          ← Back to Dashboard
        </Link>
      </div>
    );
  }

  // Group odds by market (market_type + player_name)
  const marketGroups: MarketGroup[] = [];
  const marketMap = new Map<string, OddsOffer[]>();

  for (const offer of odds || []) {
    const key = `${offer.market_type}|${offer.player_name || ''}`;
    if (!marketMap.has(key)) marketMap.set(key, []);
    marketMap.get(key)!.push(offer);
  }

  for (const [key, offers] of marketMap) {
    const { market_type: marketType, player_name: playerName } = offers[0];
    const typeLabel = MARKET_TYPE_LABELS[marketType] || marketType;
    const title = playerName ? `${playerName} — ${typeLabel}` : typeLabel;
    marketGroups.push({ key, title, offers });
  }

  const trackedPlayers = Array.from(
    new Set(
      (odds || [])
        .map((offer) => offer.player_name)
        .filter((playerName): playerName is string => Boolean(playerName))
    )
  ).sort((a, b) => a.localeCompare(b));

  // Filter discrepancies for this match
  const matchDiscrepancies = discrepancies?.filter((d) => d.match_id === id) || [];

  return (
    <div className="space-y-6">
      <Link to="/" className="inline-flex items-center text-sm text-slate-500 transition hover:text-white">
        ← Back to Dashboard
      </Link>

      <PageShell
        eyebrow={match.league_name}
        title={`${match.home_team} vs ${match.away_team}`}
        description={`Snapshot time ${formatDateTime(match.start_time)} · Open this board to inspect every fetched market for the matchup, including players with no active discrepancy right now.`}
        aside={
          <div className="space-y-3">
            <div className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-4">
              <p className="text-sm text-slate-400">Match status</p>
              <p className="mt-2 text-2xl font-semibold text-white">{match.status}</p>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-lg border border-line-700/70 bg-ink-950 px-3 py-4 text-center">
                <div className="text-2xl font-semibold text-white">{matchDiscrepancies.length}</div>
                <div className="mt-1 text-xs text-slate-500">discrepancies</div>
              </div>
              <div className="rounded-lg border border-line-700/70 bg-ink-950 px-3 py-4 text-center">
                <div className="text-2xl font-semibold text-white">{(odds || []).length}</div>
                <div className="mt-1 text-xs text-slate-500">offers</div>
              </div>
              <div className="rounded-lg border border-line-700/70 bg-ink-950 px-3 py-4 text-center">
                <div className="text-2xl font-semibold text-white">{trackedPlayers.length}</div>
                <div className="mt-1 text-xs text-slate-500">players</div>
              </div>
            </div>
          </div>
        }
      >
        {trackedPlayers.length > 0 && (
          <section className="rounded-xl border border-line-700/70 bg-ink-900 p-5">
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 className="text-xl font-semibold text-white">Tracked players</h3>
                <p className="mt-2 text-sm leading-6 text-slate-400">
                  These player names were fetched for this matchup, including markets with no active
                  discrepancy.
                </p>
              </div>
              <span className="rounded-full border border-line-700/70 bg-ink-950 px-3 py-1 text-xs font-medium text-slate-300">
                {trackedPlayers.length} tracked
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {trackedPlayers.map((player) => (
                <span
                  key={player}
                  className="rounded-full border border-line-700/70 bg-ink-950/65 px-4 py-2 text-sm text-slate-200"
                >
                  {player}
                </span>
              ))}
            </div>
          </section>
        )}

        {marketGroups.length === 0 ? (
          <div className="rounded-xl border border-line-700/70 bg-ink-900 p-8 text-center">
            <p className="text-sm text-slate-500">No odds data available for this match yet.</p>
          </div>
        ) : (
          <section className="space-y-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 className="text-xl font-semibold text-white">Markets & odds</h3>
                <p className="mt-2 text-sm text-slate-400">
                  Full snapshot of the stored offers for this match, grouped by market and player.
                </p>
              </div>
            </div>
            {marketGroups.map((group) => (
              <OddsTable
                key={group.key}
                title={group.title}
                offers={group.offers}
                discrepancies={matchDiscrepancies}
              />
            ))}
          </section>
        )}
      </PageShell>
    </div>
  );
}
