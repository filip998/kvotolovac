"""In-memory + on-disk benchmark recorder for scrape cycles.

Each completed scrape cycle produces:
- A per-cycle JSON snapshot at ``{benchmark_dir}/cycle-YYYYMMDD-HHMMSS.json``
- A single appended NDJSON line at ``{benchmark_dir}/cycles.ndjson`` for offline analysis

The latest cycle is also held in memory so the API can return it without re-reading files.
The most recent in-memory snapshot survives until the next cycle replaces it; nothing is
queryable historically through the API by design (use the NDJSON for that).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

from ..config import settings
from ..models.schemas import CycleBenchmarkOut, ScraperBenchmarkOut

logger = logging.getLogger(__name__)


class _BookmakerAcc:
    __slots__ = (
        "duration_ms",
        "raw_items",
        "leagues_attempted",
        "leagues_failed",
    )

    def __init__(self) -> None:
        self.duration_ms: int = 0
        self.raw_items: int = 0
        self.leagues_attempted: int = 0
        self.leagues_failed: int = 0


class CycleBenchmarkRecorder:
    """Accumulates per-scraper stats for one in-flight cycle, then publishes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: CycleBenchmarkOut | None = None
        self._reset()

    def _reset(self) -> None:
        self._cycle_started_at: Optional[str] = None
        self._scrape_duration_ms: int = 0
        self._cycle_duration_ms: int = 0
        self._buckets: dict[str, _BookmakerAcc] = defaultdict(_BookmakerAcc)

    # ---- accumulation ---------------------------------------------------
    def begin_cycle(self, cycle_started_at: str) -> None:
        with self._lock:
            self._reset()
            self._cycle_started_at = cycle_started_at

    def record_scrape_task(
        self,
        *,
        bookmaker_id: str,
        duration_ms: int,
        raw_items: int,
        failed: bool,
    ) -> None:
        with self._lock:
            acc = self._buckets[bookmaker_id]
            acc.duration_ms += int(duration_ms)
            acc.raw_items += int(raw_items)
            acc.leagues_attempted += 1
            if failed:
                acc.leagues_failed += 1

    def record_phase_durations(
        self, *, scrape_duration_ms: int, cycle_duration_ms: int
    ) -> None:
        with self._lock:
            self._scrape_duration_ms = int(scrape_duration_ms)
            self._cycle_duration_ms = int(cycle_duration_ms)

    # ---- publish --------------------------------------------------------
    def publish(
        self,
        *,
        matches_per_bookmaker: dict[str, int],
        odds_per_bookmaker: dict[str, int],
        total_unique_matches: int,
    ) -> CycleBenchmarkOut:
        """Build the snapshot, replace the in-memory latest, and write files.

        ``matches_per_bookmaker`` counts matches each bookmaker contributed (the same
        match covered by N bookmakers appears in N entries — that's the whole point of
        the per-scraper view). ``total_unique_matches`` is the globally deduped count
        and matches ``len(seen_matches)`` from the scheduler cycle result.
        """
        with self._lock:
            cycle_finished_at = datetime.utcnow().isoformat()
            scrapers: list[ScraperBenchmarkOut] = []
            all_keys = set(self._buckets) | set(matches_per_bookmaker) | set(odds_per_bookmaker)
            for bm in sorted(all_keys):
                acc = self._buckets.get(bm) or _BookmakerAcc()
                attempted = acc.leagues_attempted
                failure_rate = (
                    (acc.leagues_failed / attempted) if attempted > 0 else 0.0
                )
                scrapers.append(
                    ScraperBenchmarkOut(
                        bookmaker_id=bm,
                        duration_ms=acc.duration_ms,
                        raw_items=acc.raw_items,
                        matches_after_normalization=int(
                            matches_per_bookmaker.get(bm, 0)
                        ),
                        odds_count=int(odds_per_bookmaker.get(bm, 0)),
                        leagues_attempted=attempted,
                        leagues_failed=acc.leagues_failed,
                        failure_rate=round(failure_rate, 4),
                    )
                )

            snapshot = CycleBenchmarkOut(
                cycle_started_at=self._cycle_started_at,
                cycle_finished_at=cycle_finished_at,
                scrape_duration_ms=self._scrape_duration_ms,
                cycle_duration_ms=self._cycle_duration_ms,
                total_raw_items=sum(s.raw_items for s in scrapers),
                total_matches=int(total_unique_matches),
                total_odds=sum(s.odds_count for s in scrapers),
                scrapers=scrapers,
            )
            self._latest = snapshot

        # Persist outside the lock — file IO shouldn't block recorders for the
        # next cycle, and we already snapshotted state into a Pydantic model.
        try:
            self._write_files(snapshot)
        except Exception:
            logger.exception("Failed to persist scraper benchmark snapshot")

        return snapshot

    def latest(self) -> CycleBenchmarkOut | None:
        with self._lock:
            return self._latest

    # ---- IO -------------------------------------------------------------
    def _write_files(self, snapshot: CycleBenchmarkOut) -> None:
        out_dir = Path(settings.benchmark_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Use UTC compact timestamp with microseconds so back-to-back manually
        # triggered cycles can't clobber each other's snapshot file.
        now = datetime.utcnow()
        ts = now.strftime("%Y%m%d-%H%M%S-%f")
        snapshot_path = out_dir / f"cycle-{ts}.json"

        payload = snapshot.model_dump()
        snapshot_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

        ndjson_path = out_dir / "cycles.ndjson"
        with ndjson_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True))
            f.write("\n")


recorder = CycleBenchmarkRecorder()
