# Issue #95 comparable scrape-cycle baselines

## Scope

This report captures comparable baselines using the existing scheduler benchmark pipeline after #94 added runtime metadata and subphase instrumentation.

The source report named in #95 (`docs/benchmarks/2026-05-06-full-cycle-real-ex-meridian.md`) is not present on current `origin/main`, so this report uses fresh artifacts captured from the #95 worktree and stores their raw JSON/NDJSON under this directory.

## Method

All runs used isolated temporary SQLite DBs, isolated registry JSON copies, one-off environment overrides, and explicit benchmark directories. `.env` was not edited.

Real-mode configuration:

- `SCRAPER_MODE=real`
- `BOOKMAKERS=mozzart,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet`
- Meridian excluded
- `ENABLED_SPORTS=basketball,football`
- `SCRAPE_MARKET_SCOPE=all`
- `ANALYSIS_MARKETS=["all"]`
- `SCRAPE_LOOKAHEAD_HOURS=24`
- `RATE_LIMIT_PER_SECOND=1.0`
- `MERIDIAN_RATE_LIMIT_PER_SECOND=2.0`
- detail modes: `soccerbet=partial`, `merkurxtip=partial`, `pinnbet=partial`
- no proxies configured

Mock-mode used the same bookmaker/sport/market/lookahead/detail/rate metadata with `SCRAPER_MODE=mock`.

## Raw artifacts

| Run | Snapshot | NDJSON |
|---|---|---|
| Real ex-Meridian cold | `raw/real-ex-meridian/cycle-20260506-153558-606822.json` | `raw/real-ex-meridian/cycles.ndjson` |
| Real ex-Meridian warm | `raw/real-ex-meridian/cycle-20260506-154117-633434.json` | `raw/real-ex-meridian/cycles.ndjson` |
| Mock startup | `raw/mock-all/cycle-20260506-154219-616354.json` | `raw/mock-all/cycles.ndjson` |
| Mock explicit trigger | `raw/mock-all/cycle-20260506-154454-498684.json` | `raw/mock-all/cycles.ndjson` |

## Top-level comparison

| Run | Mode | Scrape ms | Cycle ms | Downstream overhead ms | Raw items | Matches | Odds/offers |
|---|---:|---:|---:|---:|---:|---:|---:|
| Real cold | real | 185,814 | 284,454 | 98,640 | 28,280 | 618 | 23,812 |
| Real warm | real | 185,457 | 285,939 | 100,482 | 28,247 | 615 | 23,810 |
| Mock startup | mock | 2 | 226 | 224 | 219 | 5 | 219 |
| Mock explicit | mock | 1 | 185 | 184 | 219 | 5 | 219 |

The two real runs are comparable by persisted metadata and workload shape. Warm behavior did not materially reduce scrape or downstream time; cold-only canonical team creation dropped from 485 auto-created teams to 0, but total cycle time stayed effectively flat.

Mock mode is not workload-comparable to real mode because mock scrapers emit only 219 raw items, but it is useful as a shared-pipeline smoke baseline: the same orchestration finishes in under 250 ms without live network and real payload volume.

## Real-mode slowest scrapers

Per-bookmaker `duration_ms` is cumulative task duration across that scraper's enabled capabilities. It is useful for ranking scraper work, but it is not the same as scrape-phase wall-clock time and can exceed the top-level scrape duration when tasks overlap.

| Run | Bookmaker | Cumulative scraper task duration ms | Raw items | Matches | Odds/offers | HTTP logical requests | Sum rate-limit wait ms | Sum network ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Cold | 365 | 255,455 | 1,890 | 147 | 1,666 | 186 | 2,023,928 | 8,891 |
| Cold | betole | 177,266 | 1,687 | 239 | 1,687 | 175 | 332,410 | 19,293 |
| Cold | superbet | 62,274 | 4,106 | 181 | 3,324 | 41 | 200,866 | 8,450 |
| Cold | volcanobet | 37,085 | 2,585 | 159 | 2,041 | 24 | 107,208 | 2,409 |
| Cold | mozzart | 20,554 | 1,248 | 126 | 890 | 16 | 14,993 | 5,342 |
| Warm | 365 | 254,672 | 1,890 | 147 | 1,666 | 186 | 2,024,465 | 8,699 |
| Warm | betole | 176,573 | 1,687 | 239 | 1,687 | 175 | 328,364 | 22,155 |
| Warm | superbet | 55,528 | 4,104 | 181 | 3,322 | 38 | 185,577 | 8,453 |
| Warm | volcanobet | 32,339 | 2,593 | 159 | 2,041 | 22 | 102,778 | 1,979 |
| Warm | mozzart | 19,954 | 1,248 | 126 | 890 | 16 | 14,247 | 5,542 |

The rate-limit wait columns are sums across logical requests, not wall-clock duration. They are still useful for attribution: 365 and BetOle are dominated by limiter wait/request volume rather than bookmaker network latency.

## Real-mode slowest downstream phases

| Run | Phase | Duration ms |
|---|---|---:|
| Cold | `resolve_events` | 55,035 |
| Cold | `normalize_outcome_offers` | 25,707 |
| Cold | `team_auto_resolution` | 7,802 |
| Cold | `normalize_threshold_odds` | 7,264 |
| Cold | `analyze_opportunities` | 2,119 |
| Warm | `resolve_events` | 55,074 |
| Warm | `normalize_outcome_offers` | 25,093 |
| Warm | `team_auto_resolution` | 7,067 |
| Warm | `normalize_threshold_odds` | 6,240 |
| Warm | `persist_snapshot` | 4,755 |

`resolve_events` and `normalize_outcome_offers` remain the dominant downstream costs in both real runs.

## Real outcome-normalization subphases

| Run | Raw outcome offers | Normalized outcome offers | Unresolved outcome offers | Unique football events | Auto-created football teams | Event resolution ms | Pair ranking ms | Row normalization ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Cold | 13,749 | 10,496 | 1,717 | 1,746 | 485 | 18,032 | 16,137 | 6,977 |
| Warm | 13,718 | 10,496 | 1,699 | 1,743 | 0 | 17,871 | 16,040 | 6,854 |

The expensive part of outcome normalization is not cold team creation. Pair ranking and event resolution stay flat between cold and warm runs.

## Real event-resolver subphases

| Run | Candidates | Exact groups | Pair checks | Accepted fuzzy pairs | Review cases | Extract candidates ms | Football raw candidate ms | Build groups ms | Persist groups ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cold | 2,094 | 618 | 7,386 | 254 | 3,592 | 46,014 | 8,844 | 8,675 | 340 |
| Warm | 2,094 | 615 | 7,220 | 251 | 3,494 | 46,027 | 8,827 | 8,688 | 353 |

Candidate extraction is the largest event-resolver subphase. The football raw candidate component is only about 8.8 s of that, so the remaining extraction path deserves attention before assuming the grouping loop is the whole problem.

## Conclusions for the next optimization issues

1. **365 request reduction (#96) remains the best next scraper optimization.** It accounts for roughly 255 s of cumulative scraper task duration with 186 logical requests and very low summed network time relative to summed limiter wait.
2. **BetOle detail-mode work (#97) is also justified.** BetOle is stable at roughly 176-177 s with 175 logical requests.
3. **Event-resolution duplicate-work and candidate-extraction work (#100) is justified.** `resolve_events` is stable around 55 s; `extract_event_candidates_ms` is around 46 s.
4. **Outcome normalization hot-loop/cache work (#101) is justified after #100.** `normalize_outcome_offers` is stable around 25 s, with football event pair ranking around 16 s.
5. **Batch canonical team auto-creation (#99) is lower priority than expected.** It was a cold-only 485-team effect but did not move the total cycle much; warm run still spent about 25 s in outcome normalization and 55 s in event resolution.

## Comparability notes

- The real cold and warm runs are comparable by persisted benchmark metadata and nearly identical workload counts.
- The mock baseline is deliberately not used to prove real-mode performance; it only shows that the shared orchestration is fast without live network and real payload volume.
- Live bookmaker latency remains noisy. These two comparable real runs should be treated as a baseline pair, not a universal median.
