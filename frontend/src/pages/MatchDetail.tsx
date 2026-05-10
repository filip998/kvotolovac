import { useParams, Link } from 'react-router-dom';
import { useMatch, useMatchOdds, useMatchOutcomeOffers } from '../api/hooks';
import { formatDateTime } from '../utils/format';
import { eventOrMatchPath } from '../utils/routes';
import LoadingSpinner from '../components/LoadingSpinner';
import PageShell from '../components/PageShell';
import BookmakerFilterDeck from '../components/BookmakerFilterDeck';
import { useBookmakerFilter } from '../hooks/useBookmakerFilter';
import EventOddsLayout from '../components/EventOddsLayout';

export default function MatchDetail() {
  const { id } = useParams<{ id: string }>();
  const {
    selectedBookmakerIds,
    updateSelectedBookmakerIds,
    search,
  } = useBookmakerFilter();
  const { data: match, isLoading: matchLoading } = useMatch(id!);
  const { data: odds, isLoading: oddsLoading } = useMatchOdds(id!);
  const { data: outcomeOffers, isLoading: outcomeOffersLoading } = useMatchOutcomeOffers(id!);

  const filteredOdds =
    selectedBookmakerIds.length === 0
      ? odds || []
      : (odds || []).filter((offer) => selectedBookmakerIds.includes(offer.bookmaker_id));
  const filteredOutcomeOffers =
    selectedBookmakerIds.length === 0
      ? outcomeOffers || []
      : (outcomeOffers || []).filter((offer) => selectedBookmakerIds.includes(offer.bookmaker_id));

  if (matchLoading || oddsLoading || outcomeOffersLoading) return <LoadingSpinner />;

  if (!match) {
    return (
      <div className="py-16 text-center">
        <h2 className="mb-2 text-base font-semibold text-text-secondary">Match not found</h2>
        <Link to={`/${search}`} className="text-sm text-text-muted hover:text-accent">
          ← Back to Dashboard
        </Link>
      </div>
    );
  }

  const visibleOfferCount = filteredOdds.length + filteredOutcomeOffers.length;

  const lastUpdated =
    [
      ...filteredOutcomeOffers.map((o) => o.scraped_at).filter((s): s is string => Boolean(s)),
      ...filteredOdds.map((o) => o.scraped_at).filter((s): s is string => Boolean(s)),
    ].sort().slice(-1)[0] ?? null;

  return (
    <div className="space-y-6">
      <Link
        to={`/${search}`}
        className="inline-flex items-center text-sm text-text-muted transition hover:text-accent"
      >
        ← Back to Dashboard
      </Link>

      <PageShell
        eyebrow={match.league_name}
        title={`${match.home_team} vs ${match.away_team}`}
        description={`${formatDateTime(match.start_time)} · ${match.status}`}
      >
        {match.resolved_event_id && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent/35 bg-accent/[0.08] px-4 py-3">
            <div>
              <div className="text-sm font-medium text-text">This match belongs to a resolved event</div>
              <div className="text-xs text-text-muted">
                This page shows only the exact normalized match. Open the event to see all member odds.
              </div>
            </div>
            <Link
              to={eventOrMatchPath(match.id, match.resolved_event_id, search)}
              className="text-sm font-medium text-accent transition hover:text-text"
            >
              View full event →
            </Link>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-6">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-lg font-semibold text-text">{visibleOfferCount}</span>
            <span className="text-xs text-text-muted">
              {selectedBookmakerIds.length ? 'visible offers' : 'offers'}
            </span>
          </div>
        </div>

        <BookmakerFilterDeck
          selectedBookmakerIds={selectedBookmakerIds}
          onChange={updateSelectedBookmakerIds}
        />

        <EventOddsLayout
          outcomeOffers={filteredOutcomeOffers}
          oddsOffers={filteredOdds}
          lastUpdated={lastUpdated}
        />
      </PageShell>
    </div>
  );
}
