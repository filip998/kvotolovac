"""Microbenchmark for search_canonical_team_candidates (issue #125).

Verifies:
  1. The new implementation returns byte-identical results to the
     pre-#125 reference algorithm on a realistic 4500-team corpus.
  2. The new implementation is meaningfully faster (>=3x sanity floor).

Runs in-process against a temporary SQLite DB seeded with synthetic teams
and aliases.  Not a pytest test — intended to be invoked as a one-off
script that prints and exits.
"""

from __future__ import annotations

import random
import string
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import settings
from app.migrations.runner import upgrade_database
from app.services import team_registry
from app.services.team_registry import (
    CanonicalTeamCandidate,
    clear_team_registry_cache,
    create_canonical_teams_batch,
    remember_team_alias,
    search_canonical_team_candidates,
    _load_team_search_rows,
    _ensure_bootstrapped,
)
from app.services.text_normalizer import normalize_identity_text
from rapidfuzz import fuzz

CORPUS_SIZE = 4500
QUERIES = 100
SPORT = "football"


def _reference_search(raw_team_name: str, *, sport: str, limit: int):
    _ensure_bootstrapped()
    raw_key = normalize_identity_text(raw_team_name)
    if not raw_key:
        return []
    candidates: list[CanonicalTeamCandidate] = []
    for team_id, team_name, aliases in _load_team_search_rows(settings.db_path, sport):
        best_score = 0.0
        best_alias = None
        for candidate_value in (team_name, *aliases):
            candidate_key = normalize_identity_text(candidate_value)
            if not candidate_key or candidate_key == raw_key:
                continue
            score = float(
                max(
                    fuzz.token_set_ratio(raw_key, candidate_key),
                    fuzz.partial_ratio(raw_key, candidate_key),
                )
            )
            if score > best_score:
                best_score = score
                best_alias = candidate_value
        if best_score <= 0:
            continue
        candidates.append(
            CanonicalTeamCandidate(
                team_id=team_id,
                team_name=team_name,
                score=best_score,
                matched_alias=best_alias if best_alias != team_name else None,
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.team_name))[:limit]


def _random_word(rng: random.Random, *, min_len: int = 4, max_len: int = 10) -> str:
    return "".join(
        rng.choice(string.ascii_lowercase)
        for _ in range(rng.randint(min_len, max_len))
    )


def _build_corpus(rng: random.Random, *, count: int) -> list[str]:
    suffixes = ["", " FC", " United", " City", " Wanderers", " Athletic", " AC", " B"]
    seen: set[str] = set()
    names: list[str] = []
    while len(names) < count:
        word_count = rng.randint(1, 3)
        words = [_random_word(rng).capitalize() for _ in range(word_count)]
        candidate = " ".join(words) + rng.choice(suffixes)
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            names.append(candidate)
    return names


def _seed_corpus(rng: random.Random, names: list[str]) -> None:
    create_canonical_teams_batch(display_names=names, sport=SPORT)
    sample = rng.sample(names, k=min(500, len(names)))
    for canonical_name in sample:
        for _ in range(rng.randint(1, 2)):
            base = canonical_name.split()[0]
            alias = f"{base} {_random_word(rng).capitalize()}"
            try:
                remember_team_alias(
                    bookmaker_id="bench-book",
                    raw_team_name=alias,
                    team_name=canonical_name,
                    sport=SPORT,
                )
            except Exception:
                pass


def _build_queries(rng: random.Random, names: list[str]) -> list[str]:
    queries = []
    for _ in range(QUERIES):
        canonical = rng.choice(names)
        words = canonical.split()
        if not words:
            queries.append(canonical)
            continue
        mutate_idx = rng.randint(0, len(words) - 1)
        words[mutate_idx] = words[mutate_idx][:-1] if len(words[mutate_idx]) > 3 else words[mutate_idx]
        if rng.random() < 0.3 and len(words) > 1:
            words.append(_random_word(rng).capitalize())
        queries.append(" ".join(words))
    return queries


def _time_block(label: str, fn) -> tuple[float, list]:
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"  {label:<10s} {elapsed*1000:>8.1f} ms")
    return elapsed, result


def main() -> int:
    rng = random.Random(20260509)
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        settings.database_url = f"sqlite:///{db_path}"
        settings.team_registry_path = str(Path(tmp) / "registry.json")
        Path(settings.team_registry_path).write_text(
            '{"aliases": {}, "bookmaker_aliases": {}, "competition_aliases": {}, "bookmaker_competition_aliases": {}}\n',
            encoding="utf-8",
        )
        upgrade_database(str(db_path))
        clear_team_registry_cache()

        names = _build_corpus(rng, count=CORPUS_SIZE)
        _seed_corpus(rng, names)
        clear_team_registry_cache(reset_bootstrap=False)
        print(f"corpus: {CORPUS_SIZE} canonical teams (sport={SPORT})")

        queries = _build_queries(rng, names)
        print(f"queries: {len(queries)} fuzzy variants")

        # Warm caches once for both implementations
        _ = search_canonical_team_candidates(queries[0], sport=SPORT, limit=3)
        _ = _reference_search(queries[0], sport=SPORT, limit=3)

        print("\n--- equivalence check (limit=3 over all queries) ---")
        mismatches = 0
        for query in queries:
            actual = search_canonical_team_candidates(query, sport=SPORT, limit=3)
            expected = _reference_search(query, sport=SPORT, limit=3)
            if actual != expected:
                mismatches += 1
                if mismatches <= 3:
                    print(f"  MISMATCH for {query!r}:")
                    print(f"    actual={actual!r}")
                    print(f"    expected={expected!r}")
        if mismatches:
            print(f"\n!! {mismatches}/{len(queries)} queries returned non-identical results")
            return 2
        print(f"  OK: all {len(queries)} queries produce byte-identical results")

        print("\n--- timing (limit=3 over all queries) ---")
        new_elapsed, _ = _time_block(
            "new",
            lambda: [
                search_canonical_team_candidates(q, sport=SPORT, limit=3) for q in queries
            ],
        )
        ref_elapsed, _ = _time_block(
            "reference",
            lambda: [_reference_search(q, sport=SPORT, limit=3) for q in queries],
        )

        speedup = ref_elapsed / new_elapsed if new_elapsed else float("inf")
        print(f"\nspeedup: {speedup:.1f}x  ({ref_elapsed*1000:.0f}ms -> {new_elapsed*1000:.0f}ms over {len(queries)} queries)")
        if speedup < 3.0:
            print(f"!! speedup {speedup:.1f}x < 3.0x sanity floor")
            return 3
        return 0


if __name__ == "__main__":
    sys.exit(main())
