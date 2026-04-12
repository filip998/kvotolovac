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
        <h2 className="mb-2 text-base font-semibold text-text-secondary">Match not found</h2>
        <Link to="/" className="text-sm text-text-muted hover:text-accent">
          ← Back to Dashboard
        </Link>
      </div>
    );
  }

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

  const matchDiscrepancies = discrepancies?.filter((d) => d.match_id === id) || [];

  return (
    <div className="space-y-6">
      <Link to="/" className="inline-flex items-center text-sm text-text-muted transition hover:text-accent">
        ← Back to Dashboard
      </Link>

      <PageShell
        eyebrow={match.league_name}
        title={`${match.home_team} vs ${match.away_team}`}
        description={`${formatDateTime(match.start_time)} · ${match.status}`}
      >
        {/* Inline stats */}
        <div className="flex flex-wrap items-center gap-6">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-lg font-semibold text-text">{matchDiscrepancies.length}</span>
            <span className="text-xs text-text-muted">discrepancies</span>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-lg font-semibold text-text">{(odds || []).length}</span>
            <span className="text-xs text-text-muted">offers</span>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-lg font-semibold text-text">{trackedPlayers.length}</span>
            <span className="text-xs text-text-muted">players</span>
          </div>
        </div>

        {trackedPlayers.length > 0 && (
          <section>
            <h3 className="mb-3 text-[11px] font-medium uppercase tracking-wider text-text-muted">
              Tracked players
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {trackedPlayers.map((player) => (
                <span
                  key={player}
                  className="rounded-md border border-border bg-surface px-3 py-1.5 text-xs text-text-secondary"
                >
                  {player}
                </span>
              ))}
            </div>
          </section>
        )}

        {marketGroups.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center">
            <p className="text-sm text-text-muted">No odds data available for this match yet.</p>
          </div>
        ) : (
          <section className="space-y-4">
            <h3 className="text-[11px] font-medium uppercase tracking-wider text-text-muted">
              Markets & odds
            </h3>
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
