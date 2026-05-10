import { useParams, Link } from 'react-router-dom';
import { useEvent, useEventOdds, useEventOutcomeOffers } from '../api/hooks';
import { formatDateTime } from '../utils/format';
import LoadingSpinner from '../components/LoadingSpinner';
import PageShell from '../components/PageShell';
import BookmakerFilterDeck from '../components/BookmakerFilterDeck';
import { useBookmakerFilter } from '../hooks/useBookmakerFilter';
import EventOddsLayout from '../components/EventOddsLayout';

function eventTitle(homeTeam?: string | null, awayTeam?: string | null): string {
  if (homeTeam && awayTeam) return `${homeTeam} vs ${awayTeam}`;
  return homeTeam || awayTeam || 'Resolved event';
}

export default function EventDetail() {
  const { id } = useParams<{ id: string }>();
  const {
    selectedBookmakerIds,
    updateSelectedBookmakerIds,
    search,
  } = useBookmakerFilter();
  const { data: event, isLoading: eventLoading } = useEvent(id!);
  const { data: odds, isLoading: oddsLoading } = useEventOdds(id!);
  const { data: outcomeOffers, isLoading: outcomeOffersLoading } = useEventOutcomeOffers(id!);

  const filteredOdds =
    selectedBookmakerIds.length === 0
      ? odds || []
      : (odds || []).filter((offer) => selectedBookmakerIds.includes(offer.bookmaker_id));
  const filteredOutcomeOffers =
    selectedBookmakerIds.length === 0
      ? outcomeOffers || []
      : (outcomeOffers || []).filter((offer) => selectedBookmakerIds.includes(offer.bookmaker_id));

  if (eventLoading || oddsLoading || outcomeOffersLoading) return <LoadingSpinner />;

  if (!event) {
    return (
      <div className="py-16 text-center">
        <h2 className="mb-2 text-base font-semibold text-text-secondary">Event not found</h2>
        <Link to={`/${search}`} className="text-sm text-text-muted hover:text-accent">
          ← Back to Dashboard
        </Link>
      </div>
    );
  }

  const visibleOfferCount = filteredOdds.length + filteredOutcomeOffers.length;
  const title = eventTitle(event.display_home_team, event.display_away_team);

  // Latest scraped_at across the visible offers (for the wire/ticker).
  const lastUpdated =
    [
      ...filteredOutcomeOffers.map((o) => o.scraped_at).filter((s): s is string => Boolean(s)),
      ...filteredOdds.map((o) => o.scraped_at).filter((s): s is string => Boolean(s)),
    ].sort().slice(-1)[0] ?? event.updated_at ?? event.created_at ?? null;

  return (
    <div className="space-y-6">
      <Link
        to={`/${search}`}
        className="inline-flex items-center text-sm text-text-muted transition hover:text-accent"
      >
        ← Back to Dashboard
      </Link>

      <PageShell
        eyebrow={event.display_league_name ?? event.sport}
        title={title}
        description={`${formatDateTime(event.start_time)} · full resolved event`}
      >
        <div className="flex flex-wrap items-center gap-6">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-lg font-semibold text-text">{visibleOfferCount}</span>
            <span className="text-xs text-text-muted">
              {selectedBookmakerIds.length ? 'visible offers' : 'offers'}
            </span>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-lg font-semibold text-text">{event.members.length}</span>
            <span className="text-xs text-text-muted">member books</span>
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
