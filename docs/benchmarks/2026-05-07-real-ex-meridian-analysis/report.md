# Real Ex-Meridian Benchmark Analysis: 2026-05-07

## Method

This run used the official backend scheduler benchmark recorder in real scraper mode. It ran one startup scheduler cycle against isolated temporary state, then copied the generated raw benchmark artifacts into this directory.

| Field | Value |
|---|---|
| Snapshot | `raw/real-ex-meridian/cycle-20260507-215806-590026.json` |
| NDJSON | `raw/real-ex-meridian/cycles.ndjson` |
| Mode | `real` |
| Bookmakers | `mozzart,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet` |
| Excluded | `meridian` |
| Sports | `basketball,football` |
| Market scope | `all` |
| Analysis markets | `all` |
| Lookahead | `24h` |
| Global rate limit | `1.0 req/s` |
| Detail modes | `betole=partial`, `soccerbet=partial`, `merkurxtip=partial`, `pinnbet=partial` |
| Proxies | none |
| State | isolated SQLite DB and copied registry JSON files |

## Executive Summary

| Metric | Value |
|---|---:|
| Cycle started | `2026-05-07T21:55:38.582479` |
| Cycle finished | `2026-05-07T21:58:06.589447` |
| Cycle duration | `148.006s` |
| Scrape duration | `57.643s` |
| Downstream overhead | `90.363s` |
| Raw fetched items | `31,632` |
| Normalized matches | `823` |
| Normalized odds/offers | `26,850` |
| HTTP logical calls | `145` |
| HTTP attempts | `145` |
| HTTP retries/errors | `0 / 0` |
| Opportunities found | `49` active persisted opportunities |

The backend completed reliably: every scraper task finished, there were no HTTP retries or errors, and the analysis published opportunities successfully. Performance is acceptable for a two-minute-ish one-off cycle, but not yet good if the target is fast, frequent, always-current scraping. The important issue is no longer 365 or BetOle request fan-out; the bottlenecks have shifted to Superbet/VolcanoBet scrape volume, event candidate extraction, opportunity analysis, and matching quality.

Compared with the previous optimized warm real ex-Meridian run from `issue-102-final-verification`, this run had a much larger live workload and was slower across most phases:

| Metric | Previous warm | This run | Change |
|---|---:|---:|---:|
| Scrape ms | `31,706` | `57,643` | `+81.8%` |
| Cycle ms | `64,146` | `148,006` | `+130.7%` |
| Raw items | `19,294` | `31,632` | `+63.9%` |
| Matches | `471` | `823` | `+74.7%` |
| Odds/offers | `16,404` | `26,850` | `+63.7%` |

This is partly workload drift from live bookmaker payloads, not necessarily a code regression. Still, `analyze_opportunities` scaling is suspicious: it increased from `1.335s` to `21.536s`, far more than the raw offer count increased.

## Phase Timings

| Phase | Time | Share | Previous warm | Change |
|---|---:|---:|---:|---:|
| scrape | `57.643s` | `38.9%` | `31.706s` | `+81.8%` |
| resolve_events | `33.345s` | `22.5%` | `14.442s` | `+130.9%` |
| analyze_opportunities | `21.536s` | `14.6%` | `1.335s` | `+1513.2%` |
| normalize_outcome_offers | `20.900s` | `14.1%` | `6.786s` | `+208.0%` |
| team_auto_resolution | `7.091s` | `4.8%` | `4.607s` | `+53.9%` |
| normalize_threshold_odds | `6.611s` | `4.5%` | `4.152s` | `+59.2%` |
| persist_snapshot | `0.845s` | `0.6%` | `1.015s` | `-16.7%` |
| register_bookmakers | `0.007s` | `0.0%` | `0.027s` | `-74.1%` |
| publish_opportunities | `0.003s` | `0.0%` | `0.060s` | `-95.0%` |
| notify_opportunities | `0.000s` | `0.0%` | `0.001s` | `-100.0%` |

The scrape phase is still the largest wall-clock component, but downstream work is now the larger combined problem: `90.363s` after scraping. Event resolution and outcome normalization are expected to grow with football volume. Opportunity analysis is the standout anomaly because its runtime grew much faster than the workload.

## Scraper Performance

Per-bookmaker duration is cumulative task duration, so it can exceed scrape wall time because scraper tasks overlap.

| Bookmaker | Duration | HTTP | Rate wait | Network | Raw | Matches | Odds/offers | Fail |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| superbet | `80.021s` | `58` | `269.236s` | `15.598s` | `5,451` | `261` | `4,283` | `0.0%` |
| volcanobet | `47.624s` | `34` | `154.007s` | `3.076s` | `3,337` | `253` | `2,586` | `0.0%` |
| mozzart | `23.031s` | `19` | `15.211s` | `7.659s` | `1,945` | `216` | `1,407` | `0.0%` |
| oktagonbet | `13.614s` | `7` | `18.237s` | `4.759s` | `3,537` | `379` | `3,521` | `0.0%` |
| maxbet | `9.897s` | `6` | `9.493s` | `1.555s` | `2,449` | `180` | `2,150` | `0.0%` |
| soccerbet | `4.445s` | `3` | `3.577s` | `2.103s` | `2,898` | `263` | `2,826` | `0.0%` |
| pinnbet | `4.017s` | `3` | `3.263s` | `0.599s` | `2,421` | `248` | `2,346` | `0.0%` |
| merkurxtip | `3.544s` | `3` | `3.288s` | `0.241s` | `1,305` | `154` | `1,195` | `0.0%` |
| betole | `3.010s` | `3` | `3.589s` | `0.571s` | `1,773` | `382` | `1,768` | `0.0%` |
| admiralbet | `2.920s` | `3` | `3.571s` | `0.450s` | `2,067` | `220` | `1,932` | `0.0%` |
| 365 | `2.850s` | `3` | `3.597s` | `0.400s` | `2,497` | `219` | `2,000` | `0.0%` |
| balkanbet | `2.781s` | `3` | `3.577s` | `0.391s` | `1,952` | `56` | `836` | `0.0%` |

Superbet and VolcanoBet now dominate scrape-side cost. That is mostly request count plus conservative pacing, not failures. 365 and BetOle look healthy after the previous request-volume optimizations: each completed in roughly three seconds with only three logical requests.

## Sport And Market Coverage

| Sport | Raw | Matches | Odds/offers | Matched events | Not matched | Match rate | In review |
|---|---:|---:|---:|---:|---:|---:|---:|
| basketball | `11,937` | `715` | `11,386` | `641` | `74` | `89.65%` | `213` |
| football | `19,695` | `2,116` | `15,464` | `2,114` | `2` | `99.91%` | `235` |

Football resolves almost everything by event count, but it still generates a lot of team-review debt. Basketball has lower event match coverage and more visible split/alias problems.

Persisted market volume from the isolated DB:

| Source | Sport | Market | Persisted rows |
|---|---|---|---:|
| threshold | basketball | `game_total_ot` | `4,086` |
| threshold | basketball | `home_handicap_ot` | `3,034` |
| threshold | basketball | `player_points` | `831` |
| threshold | basketball | `player_points_milestones` | `704` |
| threshold | basketball | `player_rebounds` | `515` |
| threshold | basketball | `player_assists` | `387` |
| threshold | basketball | `player_3points` | `380` |
| outcome | football | `football_result` | `6,345` |
| outcome | football | `football_double_chance` | `4,784` |
| outcome | football | `football_total_goals` | `4,320` |

BetOle and PinnBet partial detail mode is visible: they contribute football result and totals, but no football double-chance rows. That is an intentional performance/coverage tradeoff, not a scrape failure.

Active opportunities were concentrated in basketball:

| Sport | Market | Type | Count |
|---|---|---|---:|
| basketball | `player_points` | `middle` | `27` |
| basketball | `player_assists` | `same_line_arbitrage` | `9` |
| basketball | `player_points` | `same_line_arbitrage` | `5` |
| basketball | `game_total_ot` | `middle` | `3` |
| basketball | `player_rebounds` | `same_line_arbitrage` | `2` |
| football | `football_total_goals` | `same_line_arbitrage` | `2` |
| basketball | `game_total_ot` | `same_line_arbitrage` | `1` |

The football pipeline produces a large amount of normalized data but only two active opportunities in this run, both total-goals arbitrage. That may be market reality, but it is also a reason to profile football offer grouping and analysis separately: high volume with low output can hide expensive low-value work.

## Matching And Resolution Quality

Outcome normalization:

| Metric | Value |
|---|---:|
| Raw outcome offers | `19,695` |
| Normalized outcome offers | `15,464` |
| Unresolved outcome offers | `740` |
| Unique football events | `2,632` |
| Auto-created football teams | `797` |
| Football event resolution | `6.881s` |
| Pair ranking | `3.940s` |
| Row normalization | `13.421s` |

Event resolver:

| Metric | Value |
|---|---:|
| Candidate events | `2,831` |
| Persisted resolved events | `518` |
| Persisted members | `2,831` |
| Exact groups | `822` |
| Pair checks | `9,949` |
| Fuzzy score calls | `19,794` |
| Accepted fuzzy pairs | `304` |
| Review cases | `122` |
| Extract candidates | `32.228s` |
| Build groups | `0.552s` |
| Persist groups | `0.362s` |

The resolver bottleneck is candidate extraction, not group building or persistence. Football raw candidate reuse is working (`105ms` football raw resolution candidate time), so the next investigation should split `extract_event_candidates_ms` into its source lookups and candidate construction paths.

Worst bookmaker/sport event match rates:

| Sport | Bookmaker | Normalized events | Matched | Not matched | Match rate | In review |
|---|---|---:|---:|---:|---:|---:|
| basketball | superbet | `74` | `53` | `21` | `71.62%` | `20` |
| basketball | admiralbet | `83` | `61` | `22` | `73.49%` | `22` |
| basketball | balkanbet | `9` | `7` | `2` | `77.78%` | `4` |
| basketball | mozzart | `60` | `51` | `9` | `85.00%` | `21` |
| basketball | volcanobet | `73` | `65` | `8` | `89.04%` | `27` |
| basketball | pinnbet | `71` | `65` | `6` | `91.55%` | `17` |
| basketball | merkurxtip | `26` | `24` | `2` | `92.31%` | `9` |
| football | mozzart | `156` | `155` | `1` | `99.36%` | `20` |
| football | soccerbet | `198` | `197` | `1` | `99.49%` | `23` |

Split/overmerge diagnostics also show real matching debt:

| Diagnostic | Count |
|---|---:|
| Split candidate clusters | `103` |
| Events in split candidates | `163` |
| Members in split candidates | `643` |
| Overmerge candidate clusters | `8` |
| Events in overmerge candidates | `8` |
| Members in overmerge candidates | `76` |

Examples worth reviewing:

| Type | Sport | Example | Why it matters |
|---|---|---|---|
| split candidate | football | `Oakleigh - Dandenong T.` vs `Preston Lions - Dandenong City` | Same-side conflicting-opponent cluster; high-risk fuzzy grouping around similar `Dandenong` names. |
| split candidate | basketball | `Franklin Bulls - Nelson Giants` vs `Franklin - Nelson` | Looks like a likely duplicate split; alias normalization should merge these better. |
| split candidate | football | `Aue - Duisburg` vs `Erzgebirge Aue - MSV Duisburg` | Likely duplicate split across abbreviated/full team names. |
| overmerge candidate | football | `Dortmund - Eintracht Frankfurt` | Large 12-bookmaker fuzzy group flagged as possible conflicting-member overmerge; should be sampled manually. |
| overmerge candidate | basketball | `Cividale - Rieti` | Large 10-bookmaker fuzzy group flagged as possible conflicting-member overmerge. |

## Anomalies

Unresolved rows and review cases are the main quality problem.

| Area | Observation |
|---|---|
| Football unresolved rows | `738` unresolved `football_result` rows, mostly unresolved home/away team names. |
| Top unresolved books | Superbet `235`, BalkanBet `175`, VolcanoBet `127`, Mozzart `82`, 365 `47`. |
| Basketball unresolved rows | MaxBet `125` and MerkurXTip `29`, all from `no_canonical_matchup_for_team_at_slot`. |
| Oklahoma props | Normalizer logged dropped shared-platform props for Oklahoma at `2026-05-08T01:30:00+00:00` from MaxBet and MerkurXTip. |
| Team review debt | `869` team review cases were created; `740` are pending football candidate-search cases. |
| Event review debt | `122` event review cases, mostly low-confidence possible event equivalence. |
| Auto-created teams | `797` football teams auto-created in this isolated cold state. This may be lower on warm app state, but it shows the committed registry is not enough to avoid heavy football bootstrap work. |
| Opportunity analysis | `21.536s` for only `49` active opportunities is the biggest performance anomaly. |

Team review distribution confirms the same pattern:

| Sport | Review kind | Reason | Status | Count |
|---|---|---|---|---:|
| football | `candidate_search` | `candidate_team_search` | pending | `448` |
| football | `candidate_search` | `candidate_team_match_same_start_time` | pending | `292` |
| basketball | `alias_suggestion` | `candidate_team_match_same_start_time` | pending | `72` |
| basketball | `auto_canonical_merge_suggestion` | `same_time_both_sides_canonical_merge` | approved | `21` |
| basketball | `candidate_search` | `candidate_team_match_same_start_time` | pending | `20` |
| basketball | `auto_alias_suggestion` | `candidate_team_match_same_start_time` | approved | `16` |

## Assessment

The backend is operationally solid in this run: it completed a broad real scrape without failed scraper tasks, HTTP retries, or HTTP errors. The request-volume optimizations for 365 and BetOle are clearly holding. Persistence and publishing are no longer meaningful bottlenecks.

It is not yet as efficient or as clean as it should be. A `148s` cycle with `90s` downstream overhead is heavy, and the matching/review debt is visible enough that opportunity quality can still be improved. The main performance concern is not raw network latency; it is the amount of work being done after data is fetched, especially event candidate extraction and opportunity analysis.

## Recommended Next Work

The candidates below are ordered by expected impact and by how directly this benchmark points to them.

### 1. Instrument and optimize opportunity analysis

**Evidence:** `analyze_opportunities` took `21.536s`, up from `1.335s` in the previous optimized warm run. The workload grew materially (`16,404 -> 26,850` normalized odds/offers), but not enough to explain a `15x` analysis-time jump. The run produced only `49` active opportunities, so the cost is in candidate generation/comparison rather than output volume.

**Why it matters:** Opportunity analysis is now the biggest unexpected backend cost. If this phase scales superlinearly, larger live slates will make the backend feel slow even when scraper request counts are acceptable.

**Candidate actions:**

1. Add benchmark subphase counters inside canonical/opportunity analysis: offers loaded, grouped events, market groups, subject groups, line groups, candidate pair count, same-line comparisons, middle comparisons, and emitted opportunities.
2. Report those counters by `sport`, `market_type`, and `opportunity_type`.
3. Add timers around grouping, pair generation, same-line arbitrage scoring, middle scoring, EV/ranking, and output serialization.
4. Check for accidental repeated full-market scans per match/event/market.
5. Avoid comparing bookmaker offers that cannot produce a valid pair, for example same bookmaker, incompatible line, incompatible outcome, or market types with no active analysis rule.
6. Consider per-market caps or early pruning for low-value football groups if football keeps producing high volume but low opportunity output.

**Success signal:** Analysis time should return to low single-digit seconds for this workload, or the new benchmark counters should identify one dominant market/pair loop that can be optimized next.

### 2. Split and optimize event candidate extraction

**Evidence:** `resolve_events` took `33.345s`. Inside it, `extract_event_candidates_ms` was `32.228s`, while group building was only `0.552s` and persistence only `0.362s`. Football raw candidate reuse is working: `football_raw_resolution_candidates_ms` was only `105ms`.

**Why it matters:** The expensive part is not fuzzy group construction anymore. Optimizing pair scoring or persistence first would miss the current bottleneck.

**Candidate actions:**

1. Split `extract_event_candidates_ms` into DB fetch time, raw source scan time, normalized match lookup time, source/member construction time, and bookmaker-source enrichment time.
2. Count rows scanned and rows emitted per source table: `odds`, `outcome_offers`, `matches`, `match_bookmaker_sources`, and resolved-event history if used.
3. Verify whether candidate extraction performs repeated per-match/per-bookmaker DB lookups that can be batched.
4. Add indexes only if the new sub-timers show query cost, not Python object construction, is the bottleneck.
5. Cache normalized team/event display strings during one cycle if extraction repeatedly normalizes the same names.
6. Keep football raw-resolution reuse intact; it is no longer the slow part.

**Success signal:** `extract_event_candidates_ms` should drop substantially, and future reports should show whether remaining time is DB-bound or CPU-bound.

### 3. Reduce football team-review and unresolved-team debt

**Evidence:** Football produced `738` unresolved `football_result` rows. The largest contributors were Superbet `235`, BalkanBet `175`, VolcanoBet `127`, Mozzart `82`, and 365 `47`. Team review cases were also heavy: `869` total, including `740` pending football candidate-search cases.

**Why it matters:** Unresolved teams reduce event quality, generate review noise, and can block or weaken cross-book matching. They also add downstream work because the resolver has to handle more incomplete or low-confidence evidence.

**Candidate actions:**

1. Export the top unresolved football team names per bookmaker from this run and group them by normalized token.
2. Prioritize aliases for Superbet, BalkanBet, VolcanoBet, and Mozzart because they account for most unresolved rows.
3. Promote obvious recurring aliases into `team_registry.json` rather than relying on cold-run auto-creation.
4. Add scraper-specific cleanup for recurring prefixes/suffixes that are bookmaker formatting artifacts rather than real team names.
5. Add a targeted regression fixture for at least the top 10 unresolved football aliases once approved.
6. Separate true new teams from alias misses in the report so future runs can show whether registry quality is improving.

**Success signal:** Pending football candidate-search cases and unresolved `football_result` rows should fall sharply, without increasing split/overmerge candidates.

### 4. Improve basketball event alias normalization and matching

**Evidence:** Basketball event match rate was `89.65%`, much weaker than football's `99.91%`. Worst basketball match rates were Superbet `71.62%`, AdmiralBet `73.49%`, BalkanBet `77.78%`, Mozzart `85.00%`, and VolcanoBet `89.04%`. Split diagnostics also flagged likely duplicate abbreviations such as `Franklin Bulls - Nelson Giants` vs `Franklin - Nelson`.

**Why it matters:** Basketball is where almost all active opportunities appeared in this run. Weak event matching in basketball can hide valid cross-book opportunities, create duplicate event groups, and reduce confidence in player-prop comparisons.

**Candidate actions:**

1. Review top basketball split candidates and classify them into true duplicates, true separate events, and ambiguous cases.
2. Add aliases for obvious abbreviation/full-name pairs such as `Franklin`/`Franklin Bulls` and similar league-specific basketball naming patterns.
3. Improve same-start-time matching for basketball where one bookmaker uses city/team abbreviation and another uses full club name.
4. Add source-bookmaker diagnostics for low basketball match-rate books so the report can show whether the issue is team aliases, league scope, start-time drift, or missing match source links.
5. Add fixtures for several split-candidate examples before changing matching thresholds.

**Success signal:** Basketball match rate should rise above `95%` on comparable real runs, and split-candidate count should drop without increasing overmerge candidates.

### 5. Review split and overmerge diagnostics before broadening fuzzy matching

**Evidence:** The run produced `103` split candidate clusters and `8` overmerge candidate clusters. Examples include likely missed duplicates (`Aue - Duisburg` vs `Erzgebirge Aue - MSV Duisburg`) and high-risk similar-name clusters (`Oakleigh - Dandenong T.` vs `Preston Lions - Dandenong City`).

**Why it matters:** Matching improvements can easily trade false negatives for false positives. The overmerge candidates show that some fuzzy groups already need caution.

**Candidate actions:**

1. Manually sample the top split and overmerge candidates before changing thresholds.
2. Tag reviewed diagnostics as confirmed split, confirmed overmerge, benign duplicate, or false alarm.
3. Use confirmed cases to create regression tests around event resolver behavior.
4. Prefer alias/normalization fixes over globally lowering fuzzy thresholds.
5. Add stronger safeguards for same-side conflicting-opponent clusters, especially when common place names appear on both sides.

**Success signal:** Future runs should show fewer split candidates and no growth in overmerge candidates; confirmed overmerge examples should become regression tests.

### 6. Reduce Superbet and VolcanoBet scrape-side cost

**Evidence:** Superbet took `80.021s`, made `58` HTTP calls, and accumulated `269.236s` summed rate-limit wait. VolcanoBet took `47.624s`, made `34` HTTP calls, and accumulated `154.007s` summed rate-limit wait. All other bookmakers are now much cheaper, and 365/BetOle each used only three calls.

**Why it matters:** Scrape wall time is still the largest single phase at `57.643s`. Since requests are concurrently scheduled, the slowest top-level scraper paths define the scrape tail.

**Candidate actions:**

1. Break Superbet and VolcanoBet request counts down by capability and endpoint.
2. Look for bulk/list endpoints that can replace per-event or per-market detail requests.
3. Check whether basketball and football paths can share upstream payloads or cache common event metadata during a cycle.
4. Keep rate limits conservative by default; optimize request volume before raising limits.
5. Add per-bookmaker detail-mode or market-scope options if certain expensive markets produce little opportunity value.

**Success signal:** Superbet and VolcanoBet cumulative durations and logical request counts should drop without materially reducing useful normalized coverage.

### 7. Keep partial detail modes as the default coverage/performance tradeoff

**Evidence:** BetOle completed in `3.010s` with `3` HTTP calls and `1,768` normalized odds/offers. PinnBet completed in `4.017s` with `3` HTTP calls and `2,346` normalized odds/offers. Partial mode intentionally omits their football double-chance detail rows.

**Why it matters:** The prior request-volume work is clearly paying off. Re-enabling full detail mode would likely add coverage, but it would also reintroduce request fan-out and rate-limit wait.

**Candidate actions:**

1. Keep `BETOLE_DETAIL_MODE=partial` and `PINNBET_DETAIL_MODE=partial` as operational defaults.
2. Run targeted full-mode benchmarks only when deciding whether double-chance coverage is worth the scrape-time cost.
3. If full mode is needed, isolate it with bookmaker/scrape-type rate caps rather than changing global limits.
4. Track opportunity yield by detail mode so the decision is based on value produced, not just rows fetched.

**Success signal:** Default full-cycle runs stay stable and fast for BetOle/PinnBet, while any full-mode run explicitly reports the added request cost and added opportunities.
