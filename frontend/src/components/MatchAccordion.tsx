import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Discrepancy } from '../api/types';
import { formatDateTime } from '../utils/format';
import DiscrepancyCard from './DiscrepancyCard';

interface MatchAccordionProps {
  matchId: string;
  homeTeam: string;
  awayTeam: string;
  startTime: string;
  discrepancies: Discrepancy[];
}

export default function MatchAccordion({
  matchId,
  homeTeam,
  awayTeam,
  startTime,
  discrepancies,
}: MatchAccordionProps) {
  const location = useLocation();
  const [isOpen, setIsOpen] = useState(true);

  return (
    <div className="rounded-lg border border-border bg-surface">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left transition hover:bg-surface-raised"
      >
        <div className="flex items-center gap-3">
          <span
            className={`inline-block text-xs text-text-muted transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}
          >
            ▶
          </span>
          <div>
            <Link
              to={{ pathname: `/matches/${matchId}`, search: location.search }}
              onClick={(e) => e.stopPropagation()}
              className="text-sm font-semibold text-text transition hover:text-accent"
            >
              {homeTeam} vs {awayTeam}
            </Link>
            <div className="mt-0.5 text-xs text-text-muted">
              {formatDateTime(startTime)}
            </div>
          </div>
        </div>
        <span className="font-mono text-xs text-text-secondary">
          {discrepancies.length}
        </span>
      </button>

      {isOpen && (
        <div className="space-y-3 border-t border-border px-4 pb-4 pt-3">
          {discrepancies.map((d) => (
            <DiscrepancyCard key={d.id} discrepancy={d} />
          ))}
        </div>
      )}
    </div>
  );
}
