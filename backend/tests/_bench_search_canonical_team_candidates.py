"""Microbenchmark for search_canonical_team_candidates (issues #125, #128).

Compares three implementations head-to-head on a synthetic 4500-team
corpus:

  - ``_reference_search``         : pre-#125 per-pair Python scoring loop
  - ``_batched_full_scan_search`` : post-#125 batched ``process.extract`` x 2
    over the full corpus (no prefilter)
  - ``search_canonical_team_candidates``: post-#128 token+trigram prefilter

Asserts soft equivalence (top-1 stable across implementations on at least
90 % of queries — bit-identical full-list equivalence is intentionally
NOT preserved by the prefilter; see issue #128 plan) and a hard speedup
floor (the prefilter must be at least 4x faster than the batched full
scan to make the live-cycle gate reachable).

Also records candidate-set telemetry (avg / p50 / p90 / p99 / max +
fallback firing count) so we can sanity-check the prefilter is doing the
work it claims to be doing.

Not a pytest test — intended to be invoked as a one-off script.
"""

from __future__ import annotations

import random
import statistics
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
    _collect_team_search_candidate_idxs,
    _ensure_bootstrapped,
    _load_team_review_search_choices,
    _load_team_review_search_indexes,
    _load_team_search_rows,
)
from app.services.text_normalizer import normalize_identity_text
from rapidfuzz import fuzz, process

CORPUS_SIZE = 4500
QUERIES = 100
SPORT = "football"


def _batched_full_scan_search(raw_team_name: str, *, sport: str, limit: int):
    """Verbatim copy of the post-#125 / pre-#128 implementation: batched
    ``process.extract`` x 2 over the entire corpus, no prefilter."""
    _ensure_bootstrapped()
    raw_key = normalize_identity_text(raw_team_name)
    if not raw_key:
        return []
    team_ids, team_names, candidate_values, normalized_choices = (
        _load_team_review_search_choices(settings.db_path, sport)
    )
    if not team_ids:
        return []
    row_count = len(normalized_choices)
    score_a = [0.0] * row_count
    score_b = [0.0] * row_count
    for _choice, score, idx in process.extract(
        raw_key, normalized_choices, scorer=fuzz.token_set_ratio,
        limit=row_count, score_cutoff=0.0,
    ):
        score_a[idx] = float(score)
    for _choice, score, idx in process.extract(
        raw_key, normalized_choices, scorer=fuzz.partial_ratio,
        limit=row_count, score_cutoff=0.0,
    ):
        score_b[idx] = float(score)
    best_by_team: dict[int, tuple[float, int]] = {}
    for idx in range(row_count):
        normalized = normalized_choices[idx]
        if not normalized or normalized == raw_key:
            continue
        score = score_a[idx]
        if score_b[idx] > score:
            score = score_b[idx]
        if score <= 0.0:
            continue
        team_id = team_ids[idx]
        current = best_by_team.get(team_id)
        if current is None or score > current[0]:
            best_by_team[team_id] = (score, idx)
    ranked = []
    for team_id, (best_score, best_idx) in best_by_team.items():
        canonical_name = team_names[best_idx]
        best_value = candidate_values[best_idx]
        ranked.append(
            (-best_score, canonical_name, team_id, best_score,
             best_value if best_value != canonical_name else None)
        )
    ranked.sort()
    return [
        CanonicalTeamCandidate(
            team_id=team_id, team_name=canonical_name, score=best_score,
            matched_alias=matched_alias,
        )
        for (_neg, canonical_name, team_id, best_score, matched_alias) in ranked[:limit]
    ]


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

        # Warm caches
        _ = search_canonical_team_candidates(queries[0], sport=SPORT, limit=3)
        _ = _batched_full_scan_search(queries[0], sport=SPORT, limit=3)
        _ = _reference_search(queries[0], sport=SPORT, limit=3)

        print("\n--- candidate-set telemetry (post-#128 prefilter) ---")
        team_ids, _, _, normalized_choices = _load_team_review_search_choices(
            settings.db_path, SPORT
        )
        token_index, trigram_index, idxs_by_team_id = (
            _load_team_review_search_indexes(settings.db_path, SPORT)
        )
        sizes = []
        fallback_count = 0
        for query in queries:
            raw_key = normalize_identity_text(query)
            if not raw_key:
                continue
            cand = _collect_team_search_candidate_idxs(
                raw_key,
                token_index=token_index,
                trigram_index=trigram_index,
                idxs_by_team_id=idxs_by_team_id,
                team_ids=team_ids,
                total_rows=len(normalized_choices),
            )
            sizes.append(len(cand))
            if len(cand) == len(normalized_choices):
                fallback_count += 1
        sizes.sort()
        n = len(sizes) or 1
        print(f"  total rows in corpus: {len(normalized_choices)}")
        print(f"  queries scored:       {len(sizes)}")
        print(f"  fallback firings:     {fallback_count} (full-scan because both indexes missed)")
        print(f"  avg candidates/query: {statistics.mean(sizes):.1f}")
        print(f"  p50 candidates/query: {sizes[n // 2]}")
        print(f"  p90 candidates/query: {sizes[int(n * 0.90)]}")
        print(f"  p99 candidates/query: {sizes[int(n * 0.99)]}")
        print(f"  max candidates/query: {max(sizes) if sizes else 0}")

        print("\n--- top-1 stability (limit=3) ---")
        top1_pref_vs_full = 0
        top1_full_vs_ref = 0
        results_pref = [search_canonical_team_candidates(q, sport=SPORT, limit=3) for q in queries]
        results_full = [_batched_full_scan_search(q, sport=SPORT, limit=3) for q in queries]
        results_ref = [_reference_search(q, sport=SPORT, limit=3) for q in queries]
        for actual, expected in zip(results_pref, results_full):
            if actual and expected and actual[0].team_id == expected[0].team_id:
                top1_pref_vs_full += 1
            elif not actual and not expected:
                top1_pref_vs_full += 1
        for full, ref in zip(results_full, results_ref):
            if full and ref and full[0].team_id == ref[0].team_id:
                top1_full_vs_ref += 1
            elif not full and not ref:
                top1_full_vs_ref += 1
        print(f"  prefilter == batched-full-scan top-1: {top1_pref_vs_full}/{len(queries)}")
        print(f"  batched-full-scan == reference top-1: {top1_full_vs_ref}/{len(queries)}")
        if top1_pref_vs_full < int(0.90 * len(queries)):
            print(f"!! top-1 stability {top1_pref_vs_full}/{len(queries)} < 90% — semantic regression risk")
            return 2

        print("\n--- timing (limit=3 over all queries) ---")
        pref_elapsed, _ = _time_block(
            "prefilter",
            lambda: [
                search_canonical_team_candidates(q, sport=SPORT, limit=3) for q in queries
            ],
        )
        full_elapsed, _ = _time_block(
            "full-scan",
            lambda: [_batched_full_scan_search(q, sport=SPORT, limit=3) for q in queries],
        )
        ref_elapsed, _ = _time_block(
            "reference",
            lambda: [_reference_search(q, sport=SPORT, limit=3) for q in queries],
        )

        speedup_vs_full = full_elapsed / pref_elapsed if pref_elapsed else float("inf")
        speedup_vs_ref = ref_elapsed / pref_elapsed if pref_elapsed else float("inf")
        print(
            f"\nprefilter speedup vs batched-full-scan (post-#125): {speedup_vs_full:.2f}x  "
            f"({full_elapsed*1000:.0f}ms -> {pref_elapsed*1000:.0f}ms over {len(queries)} queries)"
        )
        print(
            f"prefilter speedup vs reference (pre-#125):           {speedup_vs_ref:.2f}x  "
            f"({ref_elapsed*1000:.0f}ms -> {pref_elapsed*1000:.0f}ms over {len(queries)} queries)"
        )
        if speedup_vs_full < 4.0:
            print(f"!! speedup vs post-#125 {speedup_vs_full:.2f}x < 4.0x sanity floor (live-cycle gate likely unreachable)")
            return 3
        return 0


if __name__ == "__main__":
    sys.exit(main())
