# Main real benchmark deep analysis: 2026-05-08

## Executive summary

The May 7 optimization issues worked: on comparable real ex-Meridian scope, the backend now processes a much larger live slate faster than the source report. The best instrumented explicit cycle in this analysis finished in `94.775s` versus `148.006s` in the May 7 source report while processing `42,967` normalized odds/offers versus `26,850`.

The current bottleneck is no longer opportunity analysis or event candidate extraction. The dominant remaining pattern is normalization churn:

1. `normalize_outcome_offers` costs about `20.6s` for one full pass.
2. When team auto-resolution applies aliases/merges, the scheduler reruns the whole normalization pipeline, including threshold odds and outcome offers. That adds roughly another `25s` on this workload.
3. Inside one outcome-normalization pass, the largest cost is not iterating `30k` football outcome rows. It is `team_review_proxy_ms`: running team-review diagnostic normalization for only about `734` unresolved-event proxy rows costs about `13.3s` per pass.
4. Football event pair ranking is visible but secondary: about `5.8s` per pass, driven by `355,379` cross-book event-pair candidates and `1,033,287` fuzzy team-score calls.

The system is now in a good place to optimize with evidence. The highest-impact next work is to avoid or reduce full re-normalization after auto-resolution, and then split the team-review proxy path inside `normalize_odds_with_diagnostics`.

## Run details

| Field | Value |
|---|---|
| Base commit | `084e74f` |
| Working tree | included the benchmark instrumentation described below |
| Mode | real |
| Bookmakers | `mozzart,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet` |
| Excluded | Meridian |
| Sports | basketball, football |
| Market scope | all |
| Analysis markets | all |
| Lookahead | 24h |
| Detail modes | BetOle partial, MerkurXTip partial, PinnBet partial, SoccerBet partial |
| Benchmark directory | `/tmp/kvotolovac-main-real-deep-analysis-20260508-1040/benchmarks` |
| SQLite DB | `/tmp/kvotolovac-main-real-deep-analysis-20260508-1040/kvotolovac.db` |
| Latest API snapshot | `/tmp/kvotolovac-main-real-deep-analysis-20260508-1040/latest-benchmark-api.json` |

Three cycles were captured in the same isolated run directory: backend startup, explicit trigger 1, and explicit trigger 2. The second explicit trigger is the cleanest current steady-state signal because it did not need the second normalization pass.

## Instrumentation added during this investigation

The existing benchmark already had enough top-level evidence to show that `normalize_outcome_offers` was the new bottleneck, but it did not explain why. The benchmark recorder now includes:

| Area | New fields |
|---|---|
| Outcome normalization passes | `run_details[]` with per-pass wall time, row counts, football event-resolution timers, row-normalization timers, proxy rows, and direct/event-resolution counts |
| Outcome normalization row path | `team_review_proxy_ms`, `team_review_proxy_rows`, `row_iteration_ms`, `league_resolution_ms`, `event_resolution_offer_build_ms`, `direct_team_resolution_ms`, `unresolved_context_ms` |
| Outcome normalization rows by bookmaker | `bookmakers[]` with raw rows, normalized rows, event-resolution rows, direct-resolution rows, skipped unresolved rows, unresolved diagnostics, and unsupported reversed rows |
| Football event resolution hotspots | `top_football_event_buckets[]` with kickoff/start-time event counts and cross-book pair candidates |
| Event resolver source matching | `top_source_match_slots[]`, `source_match_max_sources_per_lookup`, and `source_match_truncated_slot_count` |

Timer hierarchy note: `football_event_pair_ranking_ms` and `football_event_slot_lookup_ms` are children of `football_event_resolution_ms`. `team_review_proxy_ms`, `row_iteration_ms`, and the row-loop micro-timers are children of `row_normalization_ms`. They should not be added to the parent timers as independent phases.

## Before/after comparison

Compared with `docs/benchmarks/2026-05-07-real-ex-meridian-analysis/report.md`:

| Metric | May 7 source | May 8 best instrumented cycle | Change |
|---|---:|---:|---:|
| Cycle duration | `148.006s` | `94.775s` | `-36.0%` |
| Scrape duration | `57.643s` | `41.563s` | `-27.9%` |
| Downstream overhead | `90.363s` | `53.212s` | `-41.1%` |
| Raw items | `31,632` | `49,041` | `+55.0%` |
| Normalized odds/offers | `26,850` | `42,967` | `+60.0%` |
| Opportunities | `49` | `123` | `+74` |
| Cycle ms per normalized offer | `5.51` | `2.21` | `-59.8%` |

The old source report identified `analyze_opportunities` and `extract_event_candidates` as suspicious scaling problems. Those are now mostly fixed:

| Hotspot | May 7 source | May 8 best instrumented cycle | Change |
|---|---:|---:|---:|
| `analyze_opportunities` | `21.536s` | `3.641s` | `-83.1%` |
| `resolve_events` | `33.345s` | `8.320s` | `-75.0%` |
| `extract_event_candidates` | `32.228s` | `6.331s` | `-80.4%` |
| Superbet scraper | `80.021s / 58 req` | `54.101s / 42 req` | faster, fewer requests |
| VolcanoBet scraper | `47.624s / 34 req` | `29.803s / 21 req` | faster, fewer requests |

Cross-day real scraper comparisons are noisy because bookmaker payloads and network timing change. The strongest signal is that the same code now handles about `60%` more normalized rows with much less post-scrape time.

## Same-config instrumented cycle history

| Cycle | Scrape | Cycle | Downstream | Raw | Odds/offers | Opportunities | Outcome runs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Startup | `43.964s` | `141.561s` | `97.597s` | `49,019` | `42,939` | `141` | 2 |
| Explicit trigger 1 | `41.515s` | `120.339s` | `78.824s` | `49,039` | `42,965` | `119` | 2 |
| Explicit trigger 2 | `41.563s` | `94.775s` | `53.212s` | `49,041` | `42,967` | `123` | 1 |

The `94.775s` cycle is important: it proves the same live workload can finish under 100s when a second full normalization pass is not needed. The difference between explicit trigger 1 and trigger 2 is almost exactly the cost of rerunning normalization:

| Phase | Trigger 1 | Trigger 2 | Difference |
|---|---:|---:|---:|
| `normalize_outcome_offers` | `41.633s` | `20.582s` | `+21.051s` |
| `normalize_threshold_odds` | `8.769s` | `4.317s` | `+4.452s` |
| Total extra normalization | | | `+25.503s` |

That second pass is currently triggered when team auto-resolution applies aliases or canonical merges after the first normalization pass. The first pass generates the review evidence; then auto-resolution changes registry state; then the scheduler reruns `_normalize_pipeline_batch()` on the full raw odds and outcome offer lists so current-cycle output benefits from those changes.

## Current phase profile

Best instrumented cycle:

| Phase | Duration | Share of cycle |
|---|---:|---:|
| scrape | `41.563s` | `43.9%` |
| normalize_outcome_offers | `20.582s` | `21.7%` |
| team_auto_resolution | `10.731s` | `11.3%` |
| resolve_events | `8.320s` | `8.8%` |
| persist_snapshot | `5.522s` | `5.8%` |
| normalize_threshold_odds | `4.317s` | `4.6%` |
| analyze_opportunities | `3.641s` | `3.8%` |

If the second normalization pass happens, `normalize_outcome_offers` becomes approximately equal to the entire scrape phase. If it does not happen, scrape is again the largest single wall-clock phase.

## Outcome normalization deep dive

Single-pass outcome normalization in trigger 2:

| Metric | Value |
|---|---:|
| Wall time | `20.581s` |
| Raw outcome rows | `30,872` |
| Normalized outcome rows | `24,880` |
| Unresolved diagnostics | `1,051` |
| Football unique events | `4,078` |
| Cross-book event-pair candidates | `355,379` |
| Fuzzy score calls | `1,033,287` |
| Event-resolution offers normalized | `24,864` |
| Direct-resolution attempts | `6,008` |
| Direct-resolution successes | `16` |
| Skipped unresolved raw rows | `5,992` |

The new subphase timers show where the `20.581s` goes:

| Outcome subphase | Duration | Interpretation |
|---|---:|---|
| `row_normalization_ms` | `14.313s` | Parent timer for team-review proxy diagnostics + row iteration |
| `team_review_proxy_ms` | `13.313s` | Dominant child: `normalize_odds_with_diagnostics()` over `734` unresolved-event proxy rows |
| `football_event_resolution_ms` | `5.839s` | Parent timer for event-slot lookup + pair ranking |
| `football_event_pair_ranking_ms` | `3.936s` | Fuzzy matching across same-start football event candidates |
| `football_event_slot_lookup_ms` | `1.708s` | Resolving canonical slots for unique football events |
| `row_iteration_ms` | `0.847s` | Actual loop over `30,872` raw outcome rows |
| `league_resolution_ms` | `0.341s` | League lookup for outcome rows |
| `unresolved_context_ms` | `0.174s` | Candidate matchup context for unresolved diagnostics |
| `event_resolution_offer_build_ms` | `0.095s` | Building normalized offers from resolved event slots |
| `direct_team_resolution_ms` | `0.065s` | Direct fallback team resolution |

This changes the optimization target. The expensive part is not constructing `NormalizedOutcomeOffer` objects for `30k` rows. It is the team-review proxy call that turns unresolved football outcome events into team review diagnostics.

Per unresolved proxy row, the proxy path costs roughly:

```text
13.313s / 734 proxy rows = 18.1ms per proxy row
```

That is far too high for a path that can run once or twice per cycle. The next instrumentation should split `normalize_odds_with_diagnostics()` for these proxy rows into registry lookup time, candidate-search query time, candidate scoring time, and review-case object construction.

### Outcome rows by bookmaker

Latest pass:

| Bookmaker | Raw rows | Normalized | Skipped unresolved rows | Unresolved diagnostics |
|---|---:|---:|---:|---:|
| superbet | 3,967 | 2,531 | 1,436 | 323 |
| balkanbet | 3,635 | 1,874 | 1,761 | 268 |
| oktagonbet | 2,863 | 2,855 | 8 | 2 |
| volcanobet | 2,595 | 1,891 | 704 | 135 |
| merkurxtip | 2,548 | 2,238 | 310 | 48 |
| admiralbet | 2,502 | 2,208 | 294 | 42 |
| soccerbet | 2,485 | 2,358 | 127 | 20 |
| mozzart | 2,419 | 1,773 | 646 | 105 |
| maxbet | 2,295 | 2,016 | 279 | 41 |
| 365 | 2,270 | 1,883 | 387 | 58 |
| betole | 1,823 | 1,823 | 0 | 0 |
| pinnbet | 1,470 | 1,430 | 40 | 9 |

BalkanBet and Superbet are the biggest unresolved-row contributors. BetOle is clean in this pass: all partial-mode football outcome rows normalized.

### Football event pair hotspots

Football event resolution groups events by `(sport, start_time)` and compares cross-book pairs inside that bucket. The pair work is concentrated in a few kickoff slots:

| Start time | Events | Bookmakers | Cross-book pair candidates |
|---|---:|---:|---:|
| `2026-05-08T17:00:00+00:00` | 475 | 12 | 103,077 |
| `2026-05-08T16:00:00+00:00` | 379 | 12 | 65,750 |
| `2026-05-08T15:00:00+00:00` | 312 | 12 | 44,541 |
| `2026-05-08T18:30:00+00:00` | 235 | 12 | 25,298 |
| `2026-05-08T18:00:00+00:00` | 208 | 12 | 19,758 |
| `2026-05-09T05:00:00+00:00` | 201 | 12 | 18,446 |

The top three kickoff slots account for about `213k` of `355k` pair candidates, roughly `60%` of the football outcome pair-ranking work. This is a good target for pruning. Possible filters before fuzzy scoring:

1. Restrict candidate pairs by league family where reliable league normalization exists.
2. Build an index by known canonical home/away IDs for events with resolvable sides.
3. For unresolved sides, compare only events sharing at least one strong normalized token outside low-signal football words.
4. Avoid same-start all-vs-all matching when both events already have incompatible canonical slots.

## Event resolver deep dive

The May 7 bottleneck in event resolver was candidate extraction (`32.228s`). It is now `6.331s`, but the new source-match metrics show the remaining cost:

| Metric | Value |
|---|---:|
| `extract_event_candidates_ms` | `6.331s` |
| `extract_source_match_ms` | `6.035s` |
| `extract_normalized_outcome_candidates_ms` | `5.072s` |
| Source-match lookups | 4,359 |
| Raw sources scanned across lookups | 62,033 |
| Max sources for one lookup | 60 |
| Truncated source slots beyond top 20 | 981 |

The source-match timer is nested under normalized candidate extraction. For each normalized event candidate, `_best_source()` scans raw sources in the same `(bookmaker, sport, start_time)` slot and scores team-name similarity to recover source metadata.

Top source-match slots:

| Bookmaker | Sport | Start time | Lookups | Sources scanned | Avg sources/lookup |
|---|---|---|---:|---:|---:|
| superbet | football | `2026-05-08T17:00:00+00:00` | 41 | 2,460 | 60.0 |
| oktagonbet | football | `2026-05-08T17:00:00+00:00` | 40 | 1,600 | 40.0 |
| betole | football | `2026-05-08T17:00:00+00:00` | 40 | 1,600 | 40.0 |
| soccerbet | football | `2026-05-08T17:00:00+00:00` | 37 | 1,591 | 43.0 |
| balkanbet | football | `2026-05-08T17:00:00+00:00` | 29 | 1,305 | 45.0 |
| mozzart | football | `2026-05-08T17:00:00+00:00` | 29 | 1,218 | 42.0 |

Likely improvement: index raw sources by normalized team-pair key inside each slot, and try exact/near-exact source URL or team-pair lookup before scanning every source in the slot. This should reduce the `62k` source comparisons without touching fuzzy event grouping thresholds.

## Scraper status

Scrape-side optimizations held. In the latest cycle:

| Bookmaker | Duration | Logical HTTP | Raw items | Odds/offers | Rate wait | Network |
|---|---:|---:|---:|---:|---:|---:|
| superbet | `54.101s` | 42 | 6,984 | 5,548 | `184.519s` | `11.906s` |
| volcanobet | `29.803s` | 21 | 4,393 | 3,618 | `90.242s` | `2.108s` |
| mozzart | `27.208s` | 23 | 2,794 | 2,148 | `16.825s` | `10.149s` |
| oktagonbet | `14.311s` | 8 | 5,004 | 4,996 | `23.009s` | `6.211s` |
| maxbet | `10.954s` | 7 | 4,633 | 4,354 | `9.318s` | `2.508s` |
| pinnbet | `3.852s` | 3 | 3,673 | 3,633 | `2.866s` | `0.752s` |

Superbet still defines much of the scrape tail. However, with a one-pass downstream path the whole cycle is already around `95s`, so further scrape optimization is now a second-order target behind avoiding normalization reruns and reducing team-review proxy cost.

## Opportunity analysis status

Opportunity analysis is no longer a scaling bottleneck:

| Metric | Value |
|---|---:|
| Analyze phase | `3.641s` |
| Loaded offers | `58,284` |
| Candidate pairs | `711,862` |
| Publishable candidates | 230 |
| Active opportunities | 123 |

Largest rule pair counts:

| Sport | Market | Rule | Candidate pairs | Duration | Opportunities |
|---|---|---|---:|---:|---:|
| football | result | same-line arbitrage | 127,723 | `0.000s` | 0 |
| basketball | game_total_ot | line middle | 123,597 | `0.924s` | 0 |
| football | double_chance | same-line arbitrage | 78,737 | `0.000s` | 0 |
| basketball | home_handicap_ot | line middle | 74,397 | `0.528s` | 10 |
| football | football_result_double_chance | complementary outcomes | 69,254 | `0.000s` | 1 |
| football | football_total_goals | same-line arbitrage | 52,291 | `0.000s` | 4 |
| basketball | player_points | line middle | 25,774 | `0.141s` | 65 |

The high football candidate-pair counts are worth watching, but the phase duration is low enough that optimization here is not urgent.

## Matching and review debt

Latest explicit snapshot team review debt:

| Sport | Status | Kind | Reason | Count |
|---|---|---|---|---:|
| football | pending | candidate_search | candidate_team_search | 638 |
| football | pending | candidate_search | candidate_team_match_same_start_time | 413 |
| basketball | pending | alias_suggestion | candidate_team_match_same_start_time | 81 |
| basketball | pending | candidate_search | candidate_team_match_same_start_time | 42 |

Top pending football contributors:

| Bookmaker | Reason | Count |
|---|---|---:|
| superbet | candidate_team_search | 270 |
| balkanbet | candidate_team_search | 180 |
| balkanbet | candidate_team_match_same_start_time | 88 |
| volcanobet | candidate_team_search | 72 |
| volcanobet | candidate_team_match_same_start_time | 63 |
| mozzart | candidate_team_match_same_start_time | 59 |
| superbet | candidate_team_match_same_start_time | 53 |
| mozzart | candidate_team_search | 46 |

Review debt matters for both quality and speed. It reduces normalized outcome coverage, and the diagnostic generation path is the largest measured child under outcome normalization.

## What we still do not know

The new benchmark narrows the main unknowns but does not fully explain them yet:

1. `team_review_proxy_ms` is now identified as the largest child timer, but the benchmark does not split `normalize_odds_with_diagnostics()` internally. We still need query/scoring/object-construction attribution inside team review candidate search.
2. The benchmark does not record how many auto aliases/merges were applied, how many rows changed because of the second normalization pass, or how many opportunities were gained by rerunning in the same cycle.
3. `persist_snapshot` is now around `5.5s` in steady state. That is not the top bottleneck, but it lacks subphase timing for odds inserts, outcome inserts, unresolved diagnostics, team reviews, deactivation, and snapshot bookkeeping.
4. Event resolver source-match slots show where source scans concentrate, but not time per slot. If source matching stays around `6s`, add per-slot timing or a count of exact source-url hits versus fuzzy scans.

## Recommended next work

### 1. Avoid full normalization reruns when auto-resolution has low current-cycle yield

Evidence: trigger 1 and trigger 2 had nearly identical workload, but trigger 1 reran normalization and took `120.339s`; trigger 2 did one pass and took `94.775s`. The rerun added about `25.5s` across threshold and outcome normalization.

The rerun yield can be small: in trigger 1, pass 1 normalized `24,860` outcome rows and pass 2 normalized `24,868`, only `+8` rows. That is a poor exchange for `~25s`.

Possible changes:

1. Record auto-resolution yield: auto-approved aliases, applied merges, pending merges, normalized-row delta, unresolved-diagnostic delta, and opportunity delta.
2. Gate same-cycle rerun behind a meaningful-yield threshold, or defer low-yield auto-resolution benefits to the next cycle.
3. Instead of rerunning the full pipeline, reprocess only rows whose raw team names were affected by newly applied aliases/merges.
4. If threshold odds and outcome offers need different rerun policies, split `_normalize_pipeline_batch()` so football outcome reprocessing does not force basketball threshold reprocessing.

### 2. Split and optimize team-review proxy diagnostics

Evidence: one pass spends `13.313s` in `team_review_proxy_ms` for `734` proxy rows. That dominates the one-pass `20.581s` outcome-normalization wall time.

Possible changes:

1. Add benchmark timers inside `normalize_odds_with_diagnostics()` for team registry exact alias lookup, candidate-team search, candidate scoring, review-case construction, and duplicate suppression.
2. Count candidate-search rows scanned and emitted by reason/bookmaker.
3. Cache candidate-search results by `(sport, bookmaker_id, normalized_raw_team_name, start_time, counterpart)` during one normalization pass.
4. For outcome proxy rows, avoid doing expensive candidate search twice for home/away names that appear repeatedly in the same start-time slot.

### 3. Prune football event pair ranking by kickoff bucket

Evidence: the top kickoff bucket has `475` unique football events and `103,077` cross-book pair candidates; top three buckets account for about `60%` of all pair candidates.

Possible changes:

1. Partition same-start football events by normalized league family before all-vs-all pair ranking when league metadata is reliable.
2. Pre-index resolvable canonical slots and skip fuzzy scoring when canonical home/away IDs conflict.
3. Add a cheap token-overlap guard before `fuzz.token_sort_ratio()`.

### 4. Optimize event resolver source matching

Evidence: source matching is `6.035s` of `6.331s` candidate extraction, with `62,033` raw-source comparisons for `4,359` lookups.

Possible changes:

1. Index raw sources by normalized team-pair key inside `(bookmaker, sport, start_time)`.
2. Prefer exact source URL matches before fuzzy source scoring.
3. Track exact-hit/fuzzy-scan counts in the benchmark so improvements are measurable.

### 5. Add persistence subphase timing if it becomes visible again

`persist_snapshot` is around `5.5s`. It is not the top bottleneck, but after normalization is improved it may become worth splitting into insert/update/deactivation subphases.

## Verification

Focused tests run after adding instrumentation:

```bash
cd backend && ./venv/bin/pytest -q tests/test_scraper_benchmarks.py tests/test_outcome_normalizer.py tests/test_event_resolver.py
```

Result: `93 passed`.

Full backend suite:

```bash
cd backend && ./venv/bin/pytest -q
```

Result: `1234 passed`.

Code review pass: 0 findings.
