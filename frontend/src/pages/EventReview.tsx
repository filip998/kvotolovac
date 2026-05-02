import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useAcceptEventReviewCase,
  useDeclineEventReviewCase,
  useEventReviewCases,
} from '../api/hooks';
import type { EventReviewStatus } from '../api/types';
import BookmakerFilterDeck from '../components/BookmakerFilterDeck';
import EventReviewPanel from '../components/EventReviewPanel';
import PageShell from '../components/PageShell';
import { useBookmakerFilter } from '../hooks/useBookmakerFilter';

export default function EventReview() {
  const queryClient = useQueryClient();
  const {
    selectedBookmakerIds,
    updateSelectedBookmakerIds,
  } = useBookmakerFilter();
  const [sport, setSport] = useState<'basketball' | 'football'>('basketball');
  const [statusFilter, setStatusFilter] = useState<EventReviewStatus>('pending');
  const [searchQuery, setSearchQuery] = useState('');
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const {
    data: eventReviewCases,
    isLoading,
    isError,
    error,
    refetch,
  } = useEventReviewCases(
    {
      sport,
      limit: 300,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: true }
  );
  const acceptEventReviewCase = useAcceptEventReviewCase();
  const declineEventReviewCase = useDeclineEventReviewCase();

  const acceptingCaseId =
    acceptEventReviewCase.isPending ? acceptEventReviewCase.variables?.caseId ?? null : null;
  const decliningCaseId =
    declineEventReviewCase.isPending ? declineEventReviewCase.variables?.caseId ?? null : null;

  const handleAccept = (caseId: number) => {
    setActionMessage(null);
    acceptEventReviewCase.mutate(
      { caseId },
      {
        onSuccess: (result) => {
          setActionMessage(
            result.resolved_event_id
              ? `Event candidate accepted and linked as ${result.resolved_event_id}. Team aliases were not merged.`
              : 'Event candidate accepted. Team aliases were not merged.'
          );
          void queryClient.invalidateQueries({ queryKey: ['eventReviewCases'] });
          void queryClient.invalidateQueries({ queryKey: ['matches'] });
          void queryClient.invalidateQueries({ queryKey: ['opportunities'] });
          void refetch();
        },
        onError: (mutationError) => {
          setActionMessage(`Failed to accept event candidate: ${mutationError.message}`);
        },
      }
    );
  };

  const handleDecline = (caseId: number) => {
    setActionMessage(null);
    declineEventReviewCase.mutate(
      { caseId },
      {
        onSuccess: () => {
          setActionMessage(
            'Event candidate declined. The same fingerprint stays suppressed until evidence changes.'
          );
          void queryClient.invalidateQueries({ queryKey: ['eventReviewCases'] });
          void refetch();
        },
        onError: (mutationError) => {
          setActionMessage(`Failed to decline event candidate: ${mutationError.message}`);
        },
      }
    );
  };

  return (
    <PageShell
      eyebrow="Event review"
      title="Link bookmaker events without touching canonical team identity."
      description="Use this queue for game-level equivalence: whether multiple bookmaker source events are the same matchup. Team Review remains the place for aliases and canonical team merges."
    >
      <div className="space-y-6">
        <section className="space-y-4">
          <BookmakerFilterDeck
            selectedBookmakerIds={selectedBookmakerIds}
            onChange={updateSelectedBookmakerIds}
          />
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
              Sport
            </span>
            {(['basketball', 'football'] as const).map((nextSport) => (
              <button
                key={nextSport}
                type="button"
                onClick={() => setSport(nextSport)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  sport === nextSport
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                {nextSport === 'basketball' ? 'Basketball' : 'Football'}
              </button>
            ))}
          </div>
        </section>

        <EventReviewPanel
          rows={eventReviewCases ?? []}
          isLoading={isLoading}
          errorMessage={isError ? (error as Error)?.message || 'Unknown error' : null}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          statusFilter={statusFilter}
          onStatusFilterChange={setStatusFilter}
          onAccept={handleAccept}
          onDecline={handleDecline}
          acceptingCaseId={acceptingCaseId}
          decliningCaseId={decliningCaseId}
          actionMessage={actionMessage}
        />
      </div>
    </PageShell>
  );
}
