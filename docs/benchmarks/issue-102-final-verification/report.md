# Issue #102 final benchmark verification and rollout report

## Scope

This report closes the benchmark-improvement series after:

- #94 benchmark metadata/subphase instrumentation
- #95 comparable baseline capture
- #96 365 bulk-feed scraping
- #97 BetOle partial/full detail mode
- #98 scraper rate-limit policy caps
- #99 batched football canonical-team auto-creation
- #100 reuse of football outcome event resolutions
- #101 hot-loop text/scoring caches and counters

The original source report, `docs/benchmarks/2026-05-06-full-cycle-real-ex-meridian.md`, is not present on current `origin/main`. The closest comparable pre-optimization baseline on current main is therefore `docs/benchmarks/issue-95-comparable-baselines/report.md`, which was captured with the official scheduler benchmark recorder after instrumentation landed but before the optimization slices.

## Verification

| Surface | Command | Result |
|---|---|---:|
| Targeted backend regression set | `cd backend && /Users/filiptanic/code/kvotolovac/backend/venv/bin/python -m pytest -q tests/test_bookmaker365_scraper.py tests/test_betole_scraper.py tests/test_rate_limit_policy.py tests/test_scraper_benchmarks.py tests/test_event_resolver.py tests/test_outcome_normalizer.py tests/test_normalizer.py` | 226 passed |
| Full backend suite | `cd backend && /Users/filiptanic/code/kvotolovac/backend/venv/bin/python -m pytest -q` | 1117 passed |
| Frontend production build | `cd frontend && npm run build` | passed |
| Frontend lint | `cd frontend && npm run lint` | passed |

`npm ci` reported existing dependency audit findings while installing frontend dependencies in this fresh worktree (`2 moderate`, `1 high`). Build and lint passed; dependency auditing was not part of this benchmark issue.

## Benchmark method

All benchmark runs used the existing backend scheduler benchmark recorder and its on-disk `cycle-*.json` / `cycles.ndjson` artifacts. No parallel benchmark harness was introduced.

Real-mode configuration:

- `SCRAPER_MODE=real`
- `BOOKMAKERS=mozzart,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet`
- Meridian excluded
- `ENABLED_SPORTS=basketball,football`
- `SCRAPE_MARKET_SCOPE=all`
- `ANALYSIS_MARKETS=all`
- `SCRAPE_LOOKAHEAD_HOURS=24`
- `RATE_LIMIT_PER_SECOND=1.0`
- `MERIDIAN_RATE_LIMIT_PER_SECOND=2.0`
- detail modes: `betole=partial`, `soccerbet=partial`, `merkurxtip=partial`, `pinnbet=partial`
- no proxies configured

The real cold/warm pair used an isolated SQLite DB and isolated registry JSON copies. The cold run was the first complete scheduler startup cycle. The warm run was a second complete scheduler startup cycle against the same isolated DB and registry files. The scheduler records these cycles through the same official benchmark recorder used by `/api/v1/scraper-benchmarks`.

Mock mode used the same bookmaker/sport/market/lookahead/rate metadata with `SCRAPER_MODE=mock`. Targeted 365 and BetOle runs used isolated real-mode DBs and the same sport/market/lookahead/rate settings.

## Raw artifacts

| Run | Snapshot | NDJSON |
|---|---|---|
| Final real ex-Meridian cold | `raw/real-ex-meridian/cycle-20260506-173351-301161.json` | `raw/real-ex-meridian/cycles.ndjson` |
| Final real ex-Meridian warm | `raw/real-ex-meridian/cycle-20260506-173500-127866.json` | `raw/real-ex-meridian/cycles.ndjson` |
| Final mock startup | `raw/mock-all/cycle-20260506-172950-410095.json` | `raw/mock-all/cycles.ndjson` |
| Final mock explicit trigger | `raw/mock-all/cycle-20260506-172950-909878.json` | `raw/mock-all/cycles.ndjson` |
| Targeted 365 real | `raw/targeted-365/cycle-20260506-173016-363828.json` | `raw/targeted-365/cycles.ndjson` |
| Targeted BetOle real | `raw/targeted-betole/cycle-20260506-173021-088461.json` | `raw/targeted-betole/cycles.ndjson` |

Local SQLite DBs and server logs were used to derive market-breakdown counts, but are intentionally not kept as committed benchmark artifacts.

## Top-level before/after

The before values below are the #95 comparable baseline report. The after values are this report's final real ex-Meridian runs.

| Run | Scrape ms | Cycle ms | Downstream overhead ms | Raw items | Matches | Odds/offers |
|---|---:|---:|---:|---:|---:|---:|
| #95 real cold baseline | 185,814 | 284,454 | 98,640 | 28,280 | 618 | 23,812 |
| #95 real warm baseline | 185,457 | 285,939 | 100,482 | 28,247 | 615 | 23,810 |
| Final real cold | 31,789 | 64,685 | 32,896 | 19,298 | 472 | 16,408 |
| Final real warm | 31,706 | 64,146 | 32,440 | 19,294 | 471 | 16,404 |

Compared with the #95 warm baseline, the final warm run is:

- `77.6%` lower total cycle time (`285,939 -> 64,146 ms`).
- `82.9%` lower scrape time (`185,457 -> 31,706 ms`).
- `67.7%` lower downstream overhead (`100,482 -> 32,440 ms`).
- `31.1%` lower normalized odds/offer count (`23,810 -> 16,404`).

The time improvement is real, but the count deltas are not pure performance wins. BetOle now defaults to `partial`, which intentionally skips BetOle football double-chance detail requests, and live bookmaker payloads changed between runs. Treat exact normalized-count deltas as workload/coverage deltas, not only optimizer effects.

## Final real HTTP and request-volume summary

| Run | Logical requests | Attempts | Retries | Errors | Sum rate-limit wait ms | Sum network ms |
|---|---:|---:|---:|---:|---:|---:|
| Final real cold | 98 | 98 | 0 | 0 | 305,273 | 20,003 |
| Final real warm | 98 | 98 | 0 | 0 | 307,448 | 16,624 |

The source report recorded `539` logical HTTP calls. The final full run recorded `98`, while keeping the global rate limit at `1.0` and adding no default rate increases. The remaining summed wait time is still larger than summed network time because request pacing is intentionally conservative and concurrent scraper lanes each account for their own wait.

## Slowest final warm scrapers

Per-bookmaker `duration_ms` is cumulative scraper task duration across enabled lanes/capabilities. It is useful for ranking work but is not identical to top-level scrape wall time because scraper tasks overlap.

| Bookmaker | Cumulative duration ms | Raw items | Matches | Odds/offers | HTTP requests | Rate-limit wait ms | Network ms | Failure rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| superbet | 48,339 | 3,114 | 140 | 2,566 | 32 | 161,434 | 3,823 | 0.0 |
| volcanobet | 32,050 | 1,795 | 120 | 1,342 | 20 | 84,230 | 1,188 | 0.0 |
| mozzart | 19,743 | 927 | 87 | 567 | 15 | 14,827 | 4,665 | 0.0 |
| oktagonbet | 10,407 | 2,078 | 191 | 1,989 | 5 | 9,070 | 2,359 | 0.0 |
| maxbet | 8,554 | 1,844 | 125 | 1,698 | 5 | 8,818 | 1,124 | 0.0 |
| pinnbet | 4,435 | 1,700 | 139 | 1,675 | 3 | 3,786 | 549 | 0.0 |
| soccerbet | 4,230 | 1,811 | 139 | 1,795 | 3 | 4,270 | 1,496 | 0.0 |
| merkurxtip | 3,961 | 913 | 106 | 784 | 3 | 3,799 | 147 | 0.0 |
| balkanbet | 3,445 | 1,538 | 57 | 813 | 3 | 4,304 | 590 | 0.0 |
| admiralbet | 3,174 | 1,368 | 131 | 1,153 | 3 | 4,302 | 272 | 0.0 |
| betole | 3,166 | 900 | 191 | 900 | 3 | 4,307 | 283 | 0.0 |
| 365 | 3,049 | 1,306 | 103 | 1,122 | 3 | 4,301 | 128 | 0.0 |

The scrape bottleneck has moved away from 365 and BetOle. The remaining scrape-side pressure is mostly Superbet and VolcanoBet request volume/rate-limit pacing.

## 365 and BetOle targeted checks

| Targeted run | Cycle ms | Scrape ms | Raw items | Matches | Odds/offers | HTTP requests | Attempts | Rate-limit wait ms | Network ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 365 only | 3,527 | 2,554 | 1,306 | 40 | 619 | 3 | 3 | 4,437 | 273 |
| BetOle only, partial | 3,165 | 2,562 | 900 | 53 | 211 | 3 | 3 | 4,444 | 229 |

These targeted runs verify the intended request-volume reductions:

- 365 now uses the bulk football/basketball feeds: `3` logical requests in the targeted run.
- BetOle partial mode now avoids per-football-detail double-chance fan-out: `3` logical requests in the targeted run.

The BetOle targeted normalized count is lower than its full-run contribution because isolated single-bookmaker football outcome normalization has less cross-book evidence for event/team resolution. The full real run is the better source for product-level coverage.

## Downstream phases

| Phase | #95 warm baseline ms | Final warm ms | Change |
|---|---:|---:|---:|
| `resolve_events` | 55,074 | 14,442 | -73.8% |
| `normalize_outcome_offers` | 25,093 | 6,786 | -73.0% |
| `team_auto_resolution` | 7,067 | 4,607 | -34.8% |
| `normalize_threshold_odds` | 6,240 | 4,152 | -33.5% |
| `persist_snapshot` | 4,755 | 1,015 | -78.7% |
| `analyze_opportunities` | 2,119 | 1,335 | -37.0% |

The downstream improvement is visible, but it is not a pure CPU-only comparison because final live payload size is lower than #95. The subphase metrics are more useful for identifying what changed.

### Outcome normalization subphases

| Run | Raw outcome offers | Normalized outcome offers | Unresolved outcome offers | Unique football events | Auto-created teams | Event resolution ms | Pair ranking ms | Row normalization ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| #95 warm baseline | 13,718 | 10,496 | 1,699 | 1,743 | 0 | 17,871 | 16,040 | 6,854 |
| Final warm | 9,591 | 7,092 | 1,295 | 1,270 | 0 | 2,371 | 1,010 | 4,139 |

Outcome normalization now exposes pair candidate and fuzzy-score counters. In the final warm run, football event pair candidate count and fuzzy score count were both `0` because canonical/team slot lookups already resolved the encountered football events without needing fuzzy pair creation. This is a useful signal: the remaining `normalize_outcome_offers` cost is no longer dominated by football pair ranking in this run.

### Event resolver subphases

| Run | Candidates | Exact groups | Pair checks | Fuzzy score calls | Accepted fuzzy pairs | Review cases | Extract candidates ms | Football raw candidate ms | Build groups ms | Persist groups ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| #95 warm baseline | 2,094 | 615 | 7,220 | n/a | 251 | 3,494 | 46,027 | 8,827 | 8,688 | 353 |
| Final warm | 1,529 | 471 | 3,170 | 5,216 | 181 | 1,487 | 14,008 | 51 | 195 | 236 |

The clearest #100 win is `football_raw_resolution_candidates_ms`: `8,827 -> 51 ms`. That means the duplicate football outcome resolution work is gone on the scheduler path. The clearest #101 win is group-building cost: `8,688 -> 195 ms`, with benchmark-visible pair and fuzzy-score counters now present.

`extract_event_candidates_ms` remains the largest event-resolver subphase (`14,008 ms` final warm). The football raw component is no longer the cause; the next downstream investigation should look at non-football raw source matching and candidate-source selection.

## Market coverage in the final warm DB

The final warm persisted market breakdown from the isolated real DB:

| Source | Sport | Market | Count |
|---|---|---|---:|
| threshold | basketball | `game_total_ot` | 2,933 |
| threshold | basketball | `home_handicap_ot` | 2,274 |
| threshold | basketball | `player_points` | 973 |
| threshold | basketball | `player_points_milestones` | 692 |
| threshold | basketball | `player_rebounds` | 475 |
| threshold | basketball | `player_assists` | 368 |
| threshold | basketball | `player_3points` | 346 |
| threshold | basketball | `player_points_rebounds_assists` | 306 |
| threshold | basketball | `player_points_rebounds` | 259 |
| threshold | basketball | `player_points_assists` | 215 |
| threshold | basketball | `game_total` | 162 |
| threshold | basketball | `player_rebounds_assists` | 142 |
| threshold | basketball | `player_steals` | 73 |
| threshold | basketball | `player_blocks` | 57 |
| threshold | basketball | `player_turnovers` | 35 |
| outcome | football | `football_result` | 2,898 |
| outcome | football | `football_double_chance` | 2,206 |
| outcome | football | `football_total_goals` | 1,982 |

BetOle partial mode is visible in the bookmaker-level counts: BetOle contributed `413` `football_result` offers and `276` `football_total_goals` offers, but no BetOle `football_double_chance` offers. Overall double-chance coverage remains through other bookmakers (`2,206` persisted offers in the final warm DB).

## Mock shared-pipeline benchmark

| Run | Scrape ms | Cycle ms | Downstream overhead ms | Raw items | Matches | Odds/offers |
|---|---:|---:|---:|---:|---:|---:|
| Mock startup | 1 | 195 | 194 | 219 | 5 | 219 |
| Mock explicit trigger | 1 | 157 | 156 | 219 | 5 | 219 |

Mock mode is not workload-comparable to real mode. It remains useful as a shared-pipeline smoke baseline: without live network/payload volume, the scheduler path is still sub-200ms.

## Rollout notes

Facts:

- No rate limits were increased. The global rate limit stayed at `1.0`.
- Real full-cycle wall time dropped from the #95 warm baseline `285.939s` to final warm `64.146s`.
- Full-run logical HTTP requests are now `98`; 365 and BetOle are each `3` requests in the final warm full run.
- BetOle default `partial` mode is a deliberate coverage/performance tradeoff: BetOle football double-chance is absent unless `BETOLE_DETAIL_MODE=full` is enabled.
- New benchmark metadata makes rate policies, detail modes, HTTP timing, outcome-normalization subphases, event-resolver subphases, pair checks, and fuzzy-score counts visible.

Inference:

- Remaining scrape wall time is now mostly Superbet/VolcanoBet request volume and rate-limit pacing, not 365 or BetOle.
- Remaining downstream cost is mostly event candidate extraction plus team auto-resolution, not duplicate football event-resolution work.
- A future BetOle `full` run would reintroduce per-detail request volume and should be treated as a coverage-vs-speed operational decision.

Recommended next candidates:

1. Investigate Superbet and VolcanoBet request-volume reduction or batching opportunities.
2. Split `extract_event_candidates_ms` further, now that football raw candidate rebuilding is no longer material.
3. Investigate `team_auto_resolution` on large real cycles if it remains around several seconds.
4. Keep rate-limit policy defaults conservative; use the new caps only to lower/isolate bookmaker pressure unless a separate operational decision approves increases.
