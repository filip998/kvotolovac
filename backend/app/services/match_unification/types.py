from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from ...models.schemas import (
    BenchmarkEventCoverageOut,
    BenchmarkSplitDiagnosticsOut,
    MatchUnificationBenchmarkOut,
    MatchUnificationCycleStatusOut,
    MatchUnificationResolutionBenchmarkOut,
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
MATCH_UNIFICATION_RESULT_KEY = "match_unification"


class MatchUnificationInputError(ValueError):
    """Raised when the caller violates the Match Unification Interface."""


class MatchUnificationStoreStateError(RuntimeError):
    """Raised when fallback cannot be made visible after persistence state is uncertain."""


class MatchUnificationPersistenceMetricsError(MatchUnificationStoreStateError):
    """Raised when legacy persistence metrics cannot be adapted safely."""


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
class MatchUnificationPersistenceMetrics:
    resolved_events: int
    resolved_event_members: int
    review_cases: int

    @classmethod
    def from_legacy_dict(
        cls,
        value: Mapping[str, object],
    ) -> "MatchUnificationPersistenceMetrics":
        required_keys = (
            "resolved_events",
            "resolved_event_members",
            "review_cases",
        )
        missing_keys = [key for key in required_keys if key not in value]
        if missing_keys:
            joined = ", ".join(missing_keys)
            raise MatchUnificationPersistenceMetricsError(
                f"legacy Match Unification persistence metrics missing key(s): {joined}"
            )
        return cls(
            resolved_events=_required_non_negative_int(
                value["resolved_events"],
                key="resolved_events",
            ),
            resolved_event_members=_required_non_negative_int(
                value["resolved_event_members"],
                key="resolved_event_members",
            ),
            review_cases=_required_non_negative_int(
                value["review_cases"],
                key="review_cases",
            ),
        )


def _required_non_negative_int(value: object, *, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MatchUnificationPersistenceMetricsError(
            "legacy Match Unification persistence metrics key "
            f"{key!r} must be a non-negative int, got {type(value).__name__}"
        )
    if value < 0:
        raise MatchUnificationPersistenceMetricsError(
            "legacy Match Unification persistence metrics key "
            f"{key!r} must be non-negative, got {value}"
        )
    return value


@dataclass(frozen=True)
class MatchUnificationStatus:
    state: MatchUnificationState
    mode: MatchUnificationMode
    snapshot_id: str | None = None
    warnings: tuple[MatchUnificationWarning, ...] = ()
    fallback_reason: str | None = None

    @classmethod
    def pending(cls) -> "MatchUnificationStatus":
        return cls(state="pending_unification", mode="resolved_event_graph")

    @classmethod
    def unified(cls, *, snapshot_id: str) -> "MatchUnificationStatus":
        return cls(
            snapshot_id=snapshot_id,
            state="unified",
            mode="resolved_event_graph",
        )

    @classmethod
    def match_id_only(
        cls,
        *,
        snapshot_id: str,
        warning: MatchUnificationWarning,
        fallback_reason: str,
    ) -> "MatchUnificationStatus":
        return cls(
            snapshot_id=snapshot_id,
            state="match_id_only",
            mode="match_id_only",
            warnings=(warning,),
            fallback_reason=fallback_reason,
        )

    def to_cycle_status_out(self) -> MatchUnificationCycleStatusOut:
        return MatchUnificationCycleStatusOut(
            state=self.state,
            mode=self.mode,
            warnings=[warning.detail for warning in self.warnings],
            fallback_reason=self.fallback_reason,
        )


@dataclass(frozen=True)
class MatchUnificationResult:
    status: MatchUnificationStatus
    candidates: int = 0
    resolved_events: int = 0
    resolved_event_members: int = 0
    review_cases: int = 0
    benchmark: MatchUnificationResolutionBenchmarkOut = field(
        default_factory=MatchUnificationResolutionBenchmarkOut
    )
    coverage: tuple[BenchmarkEventCoverageOut, ...] = ()
    split_diagnostics: BenchmarkSplitDiagnosticsOut = field(
        default_factory=BenchmarkSplitDiagnosticsOut
    )

    def __post_init__(self) -> None:
        if self.status.snapshot_id is None:
            raise ValueError("MatchUnificationResult.status.snapshot_id is required")

    @property
    def snapshot_id(self) -> str:
        snapshot_id = self.status.snapshot_id
        if snapshot_id is None:
            raise ValueError("MatchUnificationResult.status.snapshot_id is required")
        return snapshot_id

    @property
    def mode(self) -> MatchUnificationMode:
        return self.status.mode

    @property
    def warnings(self) -> tuple[MatchUnificationWarning, ...]:
        return self.status.warnings

    @property
    def fallback_reason(self) -> str | None:
        return self.status.fallback_reason

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(warning.code for warning in self.warnings)

    def to_cycle_status_out(self) -> MatchUnificationCycleStatusOut:
        return self.status.to_cycle_status_out()

    def to_benchmark_out(self) -> MatchUnificationBenchmarkOut:
        """Build the benchmark API compatibility shape from metrics + status."""
        metric_fields = self.benchmark.model_dump(
            exclude={"state", "mode", "warnings", "fallback_reason"}
        )
        return MatchUnificationBenchmarkOut(
            **metric_fields,
            state=self.status.state,
            mode=self.status.mode,
            warnings=list(self.warning_codes),
            fallback_reason=self.status.fallback_reason,
        )


def match_unification_cycle_status_out(
    status: MatchUnificationStatus,
) -> MatchUnificationCycleStatusOut:
    if isinstance(status, MatchUnificationStatus):
        return status.to_cycle_status_out()
    raise TypeError(
        "match_unification status must be a MatchUnificationStatus, "
        f"got {type(status).__name__}"
    )


def match_unification_status_from_cycle_value(
    status: MatchUnificationStatus,
) -> MatchUnificationStatus:
    if isinstance(status, MatchUnificationStatus):
        return status
    raise TypeError(
        "match_unification status must be a MatchUnificationStatus, "
        f"got {type(status).__name__}"
    )
