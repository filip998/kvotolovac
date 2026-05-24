__all__ = [
    "InMemoryMatchUnificationStore",
    "MATCH_UNIFICATION_RESULT_KEY",
    "MatchUnification",
    "MatchUnificationInputError",
    "MatchUnificationPersistenceMetrics",
    "MatchUnificationPersistenceMetricsError",
    "MatchUnificationResult",
    "MatchUnificationRows",
    "MatchUnificationStatus",
    "MatchUnificationStoreStateError",
    "MatchUnificationWarning",
    "OddsStoreMatchUnificationAdapter",
    "PersistedScrapeSnapshot",
    "match_unification_cycle_status_out",
    "match_unification_status_from_cycle_value",
]


def __getattr__(name: str):
    if name == "MatchUnification":
        from .unifier import MatchUnification

        return MatchUnification
    if name in {"InMemoryMatchUnificationStore", "OddsStoreMatchUnificationAdapter"}:
        from .store import (
            InMemoryMatchUnificationStore,
            OddsStoreMatchUnificationAdapter,
        )

        return {
            "InMemoryMatchUnificationStore": InMemoryMatchUnificationStore,
            "OddsStoreMatchUnificationAdapter": OddsStoreMatchUnificationAdapter,
        }[name]
    if name in {
        "MatchUnificationInputError",
        "MATCH_UNIFICATION_RESULT_KEY",
        "MatchUnificationPersistenceMetrics",
        "MatchUnificationPersistenceMetricsError",
        "MatchUnificationResult",
        "MatchUnificationRows",
        "MatchUnificationStatus",
        "MatchUnificationStoreStateError",
        "MatchUnificationWarning",
        "PersistedScrapeSnapshot",
        "match_unification_cycle_status_out",
        "match_unification_status_from_cycle_value",
    }:
        from . import types

        return getattr(types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
