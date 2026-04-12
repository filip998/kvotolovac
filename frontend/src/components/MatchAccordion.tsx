import { useState } from 'react';
import { Link } from 'react-router-dom';
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
  const [isOpen, setIsOpen] = useState(true);

  return (
    <div className="rounded-xl border border-line-700/75 bg-ink-900">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between gap-4 px-4 py-4 text-left transition hover:bg-white/[0.02]"
      >
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-500">
            <span className={`inline-block transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}>
              ▶
            </span>
          </span>
          <div>
            <Link to={`/matches/${matchId}`} onClick={(e) => e.stopPropagation()} className="text-base font-semibold text-white transition hover:text-slate-200">
              {homeTeam} vs {awayTeam}
            </Link>
            <div className="mt-1 text-xs text-slate-500">
              {formatDateTime(startTime)}
            </div>
          </div>
        </div>
        <span className="rounded-full border border-line-700/70 bg-ink-850 px-3 py-1 text-xs font-medium text-slate-200">
          {discrepancies.length} {discrepancies.length === 1 ? 'discrepancy' : 'discrepancies'}
        </span>
      </button>

      {isOpen && (
        <div className="space-y-3 border-t border-line-700/60 px-4 pb-4 pt-4">
          {discrepancies.map((d) => (
            <DiscrepancyCard key={d.id} discrepancy={d} />
          ))}
        </div>
      )}
    </div>
  );
}
