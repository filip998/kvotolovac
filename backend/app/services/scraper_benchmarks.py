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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterator, Optional

from ..config import settings
from ..models.schemas import (
    CycleBenchmarkOut,
    MarketBenchmarkOut,
    ScrapeCapabilityBenchmarkOut,
    ScraperBenchmarkOut,
    ScraperRequestBenchmarkOut,
)

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


@dataclass(frozen=True)
class _CapabilityKey:
    bookmaker_id: str
    sport: str
    lane: str
    market_scope: str
    league_id: str | None


@dataclass(frozen=True)
class _RequestKey(_CapabilityKey):
    method: str


@dataclass(frozen=True)
class _ScrapeRequestContext:
    bookmaker_id: str
    sport: str
    lane: str
    market_scope: str
    league_id: str | None = None


class _RequestAcc:
    __slots__ = ("request_count", "request_attempt_count")

    def __init__(self) -> None:
        self.request_count: int = 0
        self.request_attempt_count: int = 0


_request_context: ContextVar[_ScrapeRequestContext | None] = ContextVar(
    "scraper_benchmark_request_context",
    default=None,
)


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
        self._phase_durations_ms: dict[str, int] = {}
        self._buckets: dict[str, _BookmakerAcc] = defaultdict(_BookmakerAcc)
        self._capability_buckets: dict[_CapabilityKey, _BookmakerAcc] = defaultdict(
            _BookmakerAcc
        )
        self._request_buckets: dict[_RequestKey, _RequestAcc] = defaultdict(_RequestAcc)

    # ---- accumulation ---------------------------------------------------
    def begin_cycle(self, cycle_started_at: str) -> None:
        with self._lock:
            self._reset()
            self._cycle_started_at = cycle_started_at

    def record_scrape_task(
        self,
        *,
        bookmaker_id: str,
        sport: str,
        lane: str,
        market_scope: str,
        league_id: str | None = None,
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

            capability_key = _CapabilityKey(
                bookmaker_id=bookmaker_id,
                sport=sport,
                lane=lane,
                market_scope=market_scope,
                league_id=league_id,
            )
            capability_acc = self._capability_buckets[capability_key]
            capability_acc.duration_ms += int(duration_ms)
            capability_acc.raw_items += int(raw_items)
            capability_acc.leagues_attempted += 1
            if failed:
                capability_acc.leagues_failed += 1

    def record_phase_durations(
        self,
        *,
        scrape_duration_ms: int,
        cycle_duration_ms: int,
        phase_durations_ms: dict[str, int] | None = None,
    ) -> None:
        with self._lock:
            self._scrape_duration_ms = int(scrape_duration_ms)
            self._cycle_duration_ms = int(cycle_duration_ms)
            self._phase_durations_ms = {
                phase: int(duration_ms)
                for phase, duration_ms in (phase_durations_ms or {}).items()
            }

    def record_http_request(
        self,
        *,
        method: str,
        request_count: int = 0,
        request_attempt_count: int = 0,
    ) -> None:
        context = _request_context.get()
        if context is None:
            return

        key = _RequestKey(
            bookmaker_id=context.bookmaker_id,
            sport=context.sport,
            lane=context.lane,
            market_scope=context.market_scope,
            league_id=context.league_id,
            method=method.upper(),
        )
        with self._lock:
            acc = self._request_buckets[key]
            acc.request_count += int(request_count)
            acc.request_attempt_count += int(request_attempt_count)

    # ---- publish --------------------------------------------------------
    def publish(
        self,
        *,
        matches_per_bookmaker: dict[str, int],
        odds_per_bookmaker: dict[str, int],
        total_unique_matches: int,
        market_breakdowns: list[MarketBenchmarkOut] | None = None,
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

            request_counts_by_capability: dict[_CapabilityKey, _RequestAcc] = defaultdict(
                _RequestAcc
            )
            requests: list[ScraperRequestBenchmarkOut] = []
            for key in sorted(
                self._request_buckets,
                key=lambda item: (
                    item.bookmaker_id,
                    item.sport,
                    item.lane,
                    item.market_scope,
                    item.league_id or "",
                    item.method,
                ),
            ):
                acc = self._request_buckets[key]
                request_counts_by_capability[
                    _CapabilityKey(
                        bookmaker_id=key.bookmaker_id,
                        sport=key.sport,
                        lane=key.lane,
                        market_scope=key.market_scope,
                        league_id=key.league_id,
                    )
                ].request_count += acc.request_count
                request_counts_by_capability[
                    _CapabilityKey(
                        bookmaker_id=key.bookmaker_id,
                        sport=key.sport,
                        lane=key.lane,
                        market_scope=key.market_scope,
                        league_id=key.league_id,
                    )
                ].request_attempt_count += acc.request_attempt_count
                requests.append(
                    ScraperRequestBenchmarkOut(
                        bookmaker_id=key.bookmaker_id,
                        sport=key.sport,
                        lane=key.lane,
                        market_scope=key.market_scope,
                        league_id=key.league_id,
                        method=key.method,
                        request_count=acc.request_count,
                        request_attempt_count=acc.request_attempt_count,
                    )
                )

            capabilities: list[ScrapeCapabilityBenchmarkOut] = []
            for key in sorted(
                self._capability_buckets,
                key=lambda item: (
                    item.bookmaker_id,
                    item.sport,
                    item.lane,
                    item.market_scope,
                    item.league_id or "",
                ),
            ):
                acc = self._capability_buckets[key]
                attempted = acc.leagues_attempted
                failure_rate = (
                    (acc.leagues_failed / attempted) if attempted > 0 else 0.0
                )
                request_acc = request_counts_by_capability.get(key) or _RequestAcc()
                capabilities.append(
                    ScrapeCapabilityBenchmarkOut(
                        bookmaker_id=key.bookmaker_id,
                        sport=key.sport,
                        lane=key.lane,
                        market_scope=key.market_scope,
                        league_id=key.league_id,
                        duration_ms=acc.duration_ms,
                        raw_items=acc.raw_items,
                        request_count=request_acc.request_count,
                        request_attempt_count=request_acc.request_attempt_count,
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
                phase_durations_ms=dict(self._phase_durations_ms),
                request_count=sum(r.request_count for r in requests),
                request_attempt_count=sum(r.request_attempt_count for r in requests),
                total_raw_items=sum(s.raw_items for s in scrapers),
                total_matches=int(total_unique_matches),
                total_odds=sum(s.odds_count for s in scrapers),
                scrapers=scrapers,
                capabilities=capabilities,
                requests=requests,
                markets=market_breakdowns or [],
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


@contextmanager
def scrape_request_context(
    *,
    bookmaker_id: str,
    sport: str,
    lane: str,
    market_scope: str,
    league_id: str | None = None,
) -> Iterator[None]:
    token = _request_context.set(
        _ScrapeRequestContext(
            bookmaker_id=bookmaker_id,
            sport=sport,
            lane=lane,
            market_scope=market_scope,
            league_id=league_id,
        )
    )
    try:
        yield
    finally:
        _request_context.reset(token)


def record_http_logical_request(method: str) -> None:
    recorder.record_http_request(method=method, request_count=1)


def record_http_request_attempt(method: str) -> None:
    recorder.record_http_request(method=method, request_attempt_count=1)
