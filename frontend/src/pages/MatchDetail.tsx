import { useParams, Link } from 'react-router-dom';
import { useMatch, useMatchOdds, useDiscrepancies } from '../api/hooks';
import { formatDateTime } from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import OddsTable from '../components/OddsTable';
import LoadingSpinner from '../components/LoadingSpinner';
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
        <h2 className="mb-2 text-lg font-semibold text-gray-300">Match not found</h2>
        <Link to="/" className="text-sm text-brand-400 hover:text-brand-300">
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

  // Filter discrepancies for this match
  const matchDiscrepancies = discrepancies?.filter((d) => d.match_id === id) || [];

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <Link to="/" className="inline-flex items-center text-sm text-gray-500 transition hover:text-brand-400">
        ← Back to Dashboard
      </Link>

      {/* Match Header */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-6">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-brand-400">
          {match.league_name}
        </div>
        <h2 className="mb-1 text-2xl font-bold text-white">
          {match.home_team} vs {match.away_team}
        </h2>
        <div className="flex flex-wrap items-center gap-3 text-sm text-gray-400">
          <span>{formatDateTime(match.start_time)}</span>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
              match.status === 'live'
                ? 'bg-red-500/20 text-red-400'
                : match.status === 'upcoming'
                ? 'bg-blue-500/20 text-blue-400'
                : 'bg-gray-500/20 text-gray-400'
            }`}
          >
            {match.status.toUpperCase()}
          </span>
          {matchDiscrepancies.length > 0 && (
            <span className="rounded-full bg-brand-600/20 px-2 py-0.5 text-xs font-semibold text-brand-400">
              {matchDiscrepancies.length} discrepancies found
            </span>
          )}
        </div>
      </div>

      {/* Markets */}
      {marketGroups.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-8 text-center">
          <p className="text-sm text-gray-500">No odds data available for this match yet.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <h3 className="text-lg font-bold text-white">Markets & Odds</h3>
          {marketGroups.map((group) => (
            <OddsTable
              key={group.key}
              title={group.title}
              offers={group.offers}
              discrepancies={matchDiscrepancies}
            />
          ))}
        </div>
      )}
    </div>
  );
}
