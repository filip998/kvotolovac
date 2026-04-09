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
    <div className="rounded-xl border border-gray-800 bg-gray-900/30">
      {/* Header */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition hover:bg-gray-800/30"
      >
        <div className="flex items-center gap-3">
          <span
            className={`transition-transform duration-200 text-gray-500 ${isOpen ? 'rotate-90' : ''}`}
          >
            ▶
          </span>
          <div>
            <Link
              to={`/matches/${matchId}`}
              onClick={(e) => e.stopPropagation()}
              className="font-semibold text-white transition hover:text-brand-400"
            >
              {homeTeam} vs {awayTeam}
            </Link>
            <div className="text-xs text-gray-500">{formatDateTime(startTime)}</div>
          </div>
        </div>
        <span className="rounded-full bg-brand-600/20 px-2.5 py-0.5 text-xs font-semibold text-brand-400">
          {discrepancies.length} {discrepancies.length === 1 ? 'discrepancy' : 'discrepancies'}
        </span>
      </button>

      {/* Cards */}
      {isOpen && (
        <div className="space-y-3 px-4 pb-4">
          {discrepancies.map((d) => (
            <DiscrepancyCard key={d.id} discrepancy={d} />
          ))}
        </div>
      )}
    </div>
  );
}
