from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from ...models.schemas import (
    BenchmarkEventCoverageOut,
    BenchmarkSplitDiagnosticsOut,
    MatchUnificationBenchmarkOut,
    NormalizedOdds,
    NormalizedOutcomeOffer,
    RawOddsData,
    RawOutcomeOffer,
)

MatchUnificationMode = Literal["resolved_event_graph", "match_id_only"]
MatchUnificationState = Literal[
    "pending_unification",
    "unified",
    "match_id_only",
    "unification_failed",
]


class MatchUnificationInputError(ValueError):
    """Raised when the caller violates the Match Unification Interface."""


class MatchUnificationStoreStateError(RuntimeError):
    """Raised when fallback cannot be made visible after persistence state is uncertain."""


@dataclass(frozen=True)
class PersistedScrapeSnapshot:
    id: str
    scraped_at: str
    seen_match_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class MatchUnificationRows:
    raw_odds: Sequence[RawOddsData]
    raw_outcome_offers: Sequence[RawOutcomeOffer]
    normalized_odds: Sequence[NormalizedOdds]
    normalized_outcome_offers: Sequence[NormalizedOutcomeOffer]


@dataclass(frozen=True)
class MatchUnificationWarning:
    code: str
    detail: str


@dataclass(frozen=True)
class MatchUnificationStatus:
    snapshot_id: str
    state: MatchUnificationState
    mode: MatchUnificationMode
    warnings: tuple[MatchUnificationWarning, ...] = ()
    fallback_reason: str | None = None


@dataclass(frozen=True)
class MatchUnificationResult:
    snapshot_id: str
    mode: MatchUnificationMode
    candidates: int = 0
    resolved_events: int = 0
    resolved_event_members: int = 0
    review_cases: int = 0
    benchmark: MatchUnificationBenchmarkOut = field(
        default_factory=MatchUnificationBenchmarkOut
    )
    coverage: tuple[BenchmarkEventCoverageOut, ...] = ()
    split_diagnostics: BenchmarkSplitDiagnosticsOut = field(
        default_factory=BenchmarkSplitDiagnosticsOut
    )
    warnings: tuple[MatchUnificationWarning, ...] = ()
    status: MatchUnificationStatus | None = None

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(warning.code for warning in self.warnings)
