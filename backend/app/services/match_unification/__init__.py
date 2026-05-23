__all__ = [
    "InMemoryMatchUnificationStore",
    "MatchUnification",
    "MatchUnificationInputError",
    "MatchUnificationResult",
    "MatchUnificationRows",
    "MatchUnificationStatus",
    "MatchUnificationStoreStateError",
    "MatchUnificationWarning",
    "OddsStoreMatchUnificationAdapter",
    "PersistedScrapeSnapshot",
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
        "MatchUnificationResult",
        "MatchUnificationRows",
        "MatchUnificationStatus",
        "MatchUnificationStoreStateError",
        "MatchUnificationWarning",
        "PersistedScrapeSnapshot",
    }:
        from . import types

        return getattr(types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
