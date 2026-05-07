# Full-Cycle Benchmark Report: Real Mode, Excluding Meridian

This report captures one official backend scrape-cycle benchmark after adding full-pipeline benchmark instrumentation. It is intended as an investigation artifact for comparing scrape time, downstream phase time, request count, normalization volume, persistence, event matching, and analysis cost.

## Run configuration

| Field | Value |
| --- | --- |
| Commit | `a19fc6c` |
| Mode | `real` |
| Sports | `basketball,football` |
| Bookmakers | `mozzart,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet` |
| Excluded | `meridian` |
| Benchmark source | Official backend scheduler + `GET /api/v1/scraper-benchmarks` |
| Note | Backend started a real scheduler cycle on startup, so this report uses that official full-cycle snapshot rather than triggering a second duplicate live scrape. |

## Executive summary

| Metric | Value |
| --- | ---: |
| Cycle started | `2026-05-06T13:11:15.142885` |
| Cycle finished | `2026-05-06T13:17:31.710604` |
| Scrape duration | `222,498 ms` / `222.498s` |
| Cycle duration | `376,552 ms` / `376.552s` |
| Downstream overhead | `154,054 ms` / `154.054s` |
| Raw fetched items | `33,142` |
| Normalized matches | `702` |
| Normalized odds/offers | `28,120` |
| HTTP logical calls | `539` |
| HTTP attempts | `539` |

## Key findings

- Scraping is still the largest phase: `222.498s`, `59.1%` of the official benchmark cycle.
- Downstream work is substantial: `154.054s` total overhead after scraping.
- The biggest downstream hotspot is event resolution: `75.743s`.
- Outcome-offer normalization is the second biggest downstream hotspot: `51.310s`.
- Scrape-side slowness is dominated by `365` and `betole`, which make the most HTTP calls.
- No HTTP retries were recorded in this run: logical calls and attempts are both `539`.

## Phase timings

| Phase | ms | seconds | cycle share |
| --- | --- | --- | --- |
| scrape | 222,498 | 222.498s | 59.1% |
| resolve_events | 75,743 | 75.743s | 20.1% |
| normalize_outcome_offers | 51,310 | 51.310s | 13.6% |
| team_auto_resolution | 9,591 | 9.591s | 2.5% |
| normalize_threshold_odds | 7,289 | 7.289s | 1.9% |
| persist_snapshot | 6,985 | 6.985s | 1.9% |
| analyze_opportunities | 2,422 | 2.422s | 0.6% |
| register_bookmakers | 563 | 0.563s | 0.1% |
| publish_opportunities | 128 | 0.128s | 0.0% |
| notify | 3 | 0.003s | 0.0% |
| filter_markets | 0 | 0.000s | 0.0% |
| prepare_event_resolution_batch | 0 | 0.000s | 0.0% |
| setup | 0 | 0.000s | 0.0% |

## Scraper totals ranked by duration

| Bookmaker | time | calls | attempts | raw | matches | odds | tasks | failed | fail rate | ms/raw | ms/match | ms/odd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 365 | 264.521s | 195 | 195 | 2,217 | 182 | 1,933 | 2 | 0 | 0.00 | 119.31 | 1453.41 | 136.84 |
| betole | 224.506s | 223 | 223 | 2,068 | 288 | 2,068 | 2 | 0 | 0.00 | 108.56 | 779.53 | 108.56 |
| superbet | 61.804s | 47 | 47 | 4,631 | 210 | 3,698 | 2 | 0 | 0.00 | 13.35 | 294.30 | 16.71 |
| volcanobet | 40.590s | 28 | 28 | 2,988 | 200 | 2,300 | 2 | 0 | 0.00 | 13.58 | 202.95 | 17.65 |
| mozzart | 20.954s | 17 | 17 | 1,647 | 160 | 1,165 | 2 | 0 | 0.00 | 12.72 | 130.96 | 17.99 |
| oktagonbet | 11.403s | 7 | 7 | 3,259 | 288 | 3,126 | 2 | 0 | 0.00 | 3.50 | 39.59 | 3.65 |
| maxbet | 10.530s | 7 | 7 | 3,309 | 230 | 2,969 | 2 | 0 | 0.00 | 3.18 | 45.78 | 3.55 |
| pinnbet | 3.628s | 3 | 3 | 2,577 | 223 | 2,465 | 2 | 0 | 0.00 | 1.41 | 16.27 | 1.47 |
| soccerbet | 3.499s | 3 | 3 | 2,960 | 241 | 2,791 | 2 | 0 | 0.00 | 1.18 | 14.52 | 1.25 |
| merkurxtip | 3.190s | 3 | 3 | 1,891 | 228 | 1,686 | 2 | 0 | 0.00 | 1.69 | 13.99 | 1.89 |
| balkanbet | 3.026s | 3 | 3 | 2,700 | 100 | 1,518 | 2 | 0 | 0.00 | 1.12 | 30.26 | 1.99 |
| admiralbet | 2.463s | 3 | 3 | 2,895 | 239 | 2,401 | 2 | 0 | 0.00 | 0.85 | 10.31 | 1.03 |

## API calls by bookmaker / sport / market scope

Request counts are exact at scraper-capability level. Football calls are reported under `outcome_offer` because many bookmakers return result, double chance, and totals in the same payload; splitting one request across those markets would double-count calls.

### Basketball

| Bookmaker | market scope | lane | league | time | calls | attempts | raw | failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 365 | threshold_odds | threshold_odds | basketball | 70.204s | 38 | 38 | 958 | 0 |
| admiralbet | threshold_odds | threshold_odds | basketball | 2.079s | 2 | 2 | 1,278 | 0 |
| balkanbet | threshold_odds | threshold_odds | basketball | 2.122s | 2 | 2 | 930 | 0 |
| betole | threshold_odds | threshold_odds | basketball | 2.048s | 2 | 2 | 321 | 0 |
| maxbet | threshold_odds | threshold_odds | basketball | 4.323s | 3 | 3 | 1,761 | 0 |
| merkurxtip | threshold_odds | threshold_odds | basketball | 2.071s | 2 | 2 | 356 | 0 |
| mozzart | threshold_odds | threshold_odds | basketball | 4.150s | 3 | 3 | 329 | 0 |
| oktagonbet | threshold_odds | threshold_odds | basketball | 6.185s | 4 | 4 | 1,511 | 0 |
| pinnbet | threshold_odds | threshold_odds | basketball | 2.149s | 2 | 2 | 1,747 | 0 |
| soccerbet | threshold_odds | threshold_odds | basketball | 2.113s | 2 | 2 | 1,458 | 0 |
| superbet | threshold_odds | threshold_odds | basketball | 15.443s | 10 | 10 | 2,500 | 0 |
| volcanobet | threshold_odds | threshold_odds | basketball | 13.296s | 8 | 8 | 1,363 | 0 |

### Football

| Bookmaker | market scope | lane | league | time | calls | attempts | raw | failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 365 | outcome_offer | outcome_offer |  | 194.317s | 157 | 157 | 1,259 | 0 |
| admiralbet | outcome_offer | outcome_offer |  | 0.384s | 1 | 1 | 1,617 | 0 |
| balkanbet | outcome_offer | outcome_offer |  | 0.904s | 1 | 1 | 1,770 | 0 |
| betole | outcome_offer | outcome_offer |  | 222.458s | 221 | 221 | 1,747 | 0 |
| maxbet | outcome_offer | outcome_offer |  | 6.207s | 4 | 4 | 1,548 | 0 |
| merkurxtip | outcome_offer | outcome_offer |  | 1.119s | 1 | 1 | 1,535 | 0 |
| mozzart | outcome_offer | outcome_offer |  | 16.804s | 14 | 14 | 1,318 | 0 |
| oktagonbet | outcome_offer | outcome_offer |  | 5.218s | 3 | 3 | 1,748 | 0 |
| pinnbet | outcome_offer | outcome_offer |  | 1.479s | 1 | 1 | 830 | 0 |
| soccerbet | outcome_offer | outcome_offer |  | 1.386s | 1 | 1 | 1,502 | 0 |
| superbet | outcome_offer | outcome_offer |  | 46.361s | 37 | 37 | 2,131 | 0 |
| volcanobet | outcome_offer | outcome_offer |  | 27.294s | 20 | 20 | 1,625 | 0 |

## Aggregate odds by sport / market type

| Sport | market type | raw | match buckets | odds |
| --- | --- | --- | --- | --- |
| basketball | game_total | 222 | 152 | 217 |
| basketball | game_total_ot | 3,974 | 637 | 3,852 |
| basketball | home_handicap_ot | 3,053 | 691 | 2,942 |
| basketball | player_3points | 477 | 34 | 473 |
| basketball | player_assists | 548 | 58 | 526 |
| basketball | player_blocks | 80 | 10 | 80 |
| basketball | player_points | 2,258 | 93 | 1,910 |
| basketball | player_points_assists | 306 | 37 | 292 |
| basketball | player_points_milestones | 1,723 | 28 | 1,348 |
| basketball | player_points_rebounds | 400 | 37 | 376 |
| basketball | player_points_rebounds_assists | 384 | 35 | 377 |
| basketball | player_rebounds | 765 | 57 | 731 |
| basketball | player_rebounds_assists | 181 | 21 | 174 |
| basketball | player_steals | 101 | 10 | 101 |
| basketball | player_turnovers | 40 | 4 | 40 |
| football | football_double_chance | 6,448 | 1,724 | 5,077 |
| football | football_result | 7,140 | 1,889 | 5,694 |
| football | football_total_goals | 5,042 | 1,887 | 3,910 |

## Odds by bookmaker / sport / market type

### Basketball

| Bookmaker | market type | raw | match buckets | odds |
| --- | --- | --- | --- | --- |
| 365 | game_total | 83 | 56 | 79 |
| 365 | game_total_ot | 176 | 56 | 164 |
| 365 | home_handicap_ot | 198 | 56 | 186 |
| 365 | player_3points | 32 | 4 | 32 |
| 365 | player_assists | 40 | 7 | 40 |
| 365 | player_blocks | 14 | 2 | 14 |
| 365 | player_points | 203 | 7 | 203 |
| 365 | player_points_assists | 33 | 4 | 33 |
| 365 | player_points_rebounds | 44 | 4 | 44 |
| 365 | player_points_rebounds_assists | 26 | 4 | 26 |
| 365 | player_rebounds | 54 | 7 | 54 |
| 365 | player_rebounds_assists | 26 | 4 | 26 |
| 365 | player_steals | 21 | 2 | 21 |
| 365 | player_turnovers | 8 | 2 | 8 |
| admiralbet | game_total_ot | 239 | 60 | 216 |
| admiralbet | home_handicap_ot | 220 | 60 | 197 |
| admiralbet | player_3points | 10 | 2 | 10 |
| admiralbet | player_assists | 34 | 5 | 33 |
| admiralbet | player_points | 266 | 10 | 205 |
| admiralbet | player_points_assists | 32 | 4 | 31 |
| admiralbet | player_points_milestones | 340 | 6 | 191 |
| admiralbet | player_points_rebounds | 42 | 4 | 39 |
| admiralbet | player_points_rebounds_assists | 26 | 4 | 26 |
| admiralbet | player_rebounds | 47 | 4 | 44 |
| admiralbet | player_rebounds_assists | 22 | 2 | 22 |
| balkanbet | game_total_ot | 209 | 28 | 196 |
| balkanbet | home_handicap_ot | 205 | 28 | 192 |
| balkanbet | player_3points | 35 | 4 | 35 |
| balkanbet | player_assists | 38 | 4 | 34 |
| balkanbet | player_points | 224 | 6 | 191 |
| balkanbet | player_points_assists | 35 | 4 | 31 |
| balkanbet | player_points_milestones | 53 | 5 | 43 |
| balkanbet | player_points_rebounds | 50 | 4 | 45 |
| balkanbet | player_points_rebounds_assists | 29 | 4 | 29 |
| balkanbet | player_rebounds | 52 | 4 | 47 |
| betole | game_total_ot | 68 | 68 | 68 |
| betole | home_handicap_ot | 68 | 68 | 68 |
| betole | player_assists | 40 | 7 | 40 |
| betole | player_points | 91 | 7 | 91 |
| betole | player_rebounds | 54 | 7 | 54 |
| maxbet | game_total | 84 | 42 | 84 |
| maxbet | game_total_ot | 314 | 58 | 314 |
| maxbet | home_handicap_ot | 310 | 58 | 310 |
| maxbet | player_3points | 32 | 4 | 32 |
| maxbet | player_assists | 30 | 4 | 30 |
| maxbet | player_blocks | 14 | 2 | 14 |
| maxbet | player_points | 274 | 9 | 199 |
| maxbet | player_points_assists | 29 | 4 | 29 |
| maxbet | player_points_milestones | 519 | 6 | 408 |
| maxbet | player_points_rebounds | 44 | 4 | 44 |
| maxbet | player_points_rebounds_assists | 23 | 4 | 23 |
| maxbet | player_rebounds | 44 | 4 | 44 |
| maxbet | player_rebounds_assists | 23 | 4 | 23 |
| maxbet | player_steals | 21 | 2 | 21 |
| merkurxtip | game_total_ot | 60 | 57 | 57 |
| merkurxtip | home_handicap_ot | 60 | 57 | 57 |
| merkurxtip | player_3points | 36 | 4 | 36 |
| merkurxtip | player_assists | 33 | 4 | 30 |
| merkurxtip | player_points | 93 | 6 | 74 |
| merkurxtip | player_points_rebounds_assists | 24 | 4 | 24 |
| merkurxtip | player_rebounds | 50 | 4 | 45 |
| mozzart | game_total | 55 | 54 | 54 |
| mozzart | home_handicap_ot | 55 | 54 | 54 |
| mozzart | player_assists | 28 | 4 | 28 |
| mozzart | player_points | 149 | 6 | 149 |
| mozzart | player_rebounds | 42 | 4 | 42 |
| oktagonbet | game_total_ot | 529 | 68 | 529 |
| oktagonbet | home_handicap_ot | 68 | 68 | 68 |
| oktagonbet | player_3points | 32 | 4 | 32 |
| oktagonbet | player_assists | 40 | 5 | 36 |
| oktagonbet | player_blocks | 14 | 2 | 14 |
| oktagonbet | player_points | 91 | 6 | 72 |
| oktagonbet | player_points_assists | 33 | 4 | 33 |
| oktagonbet | player_points_milestones | 533 | 6 | 428 |
| oktagonbet | player_points_rebounds | 44 | 4 | 44 |
| oktagonbet | player_points_rebounds_assists | 26 | 4 | 26 |
| oktagonbet | player_rebounds | 54 | 5 | 49 |
| oktagonbet | player_rebounds_assists | 26 | 4 | 26 |
| oktagonbet | player_steals | 21 | 2 | 21 |
| pinnbet | game_total_ot | 619 | 64 | 608 |
| pinnbet | home_handicap_ot | 560 | 64 | 551 |
| pinnbet | player_3points | 49 | 4 | 49 |
| pinnbet | player_assists | 45 | 7 | 45 |
| pinnbet | player_points | 273 | 10 | 226 |
| pinnbet | player_points_assists | 39 | 6 | 39 |
| pinnbet | player_points_rebounds | 51 | 6 | 51 |
| pinnbet | player_points_rebounds_assists | 31 | 4 | 31 |
| pinnbet | player_rebounds | 58 | 7 | 58 |
| pinnbet | player_rebounds_assists | 22 | 2 | 22 |
| soccerbet | game_total_ot | 494 | 59 | 481 |
| soccerbet | home_handicap_ot | 449 | 59 | 438 |
| soccerbet | player_3points | 34 | 3 | 30 |
| soccerbet | player_assists | 44 | 5 | 36 |
| soccerbet | player_points | 205 | 6 | 176 |
| soccerbet | player_points_assists | 44 | 5 | 36 |
| soccerbet | player_points_rebounds | 57 | 5 | 44 |
| soccerbet | player_points_rebounds_assists | 37 | 3 | 30 |
| soccerbet | player_rebounds | 57 | 5 | 44 |
| soccerbet | player_rebounds_assists | 37 | 3 | 30 |
| superbet | game_total_ot | 713 | 52 | 692 |
| superbet | home_handicap_ot | 311 | 52 | 300 |
| superbet | player_3points | 217 | 5 | 217 |
| superbet | player_assists | 176 | 6 | 174 |
| superbet | player_blocks | 38 | 4 | 38 |
| superbet | player_points | 128 | 11 | 115 |
| superbet | player_points_assists | 61 | 6 | 60 |
| superbet | player_points_milestones | 278 | 5 | 278 |
| superbet | player_points_rebounds | 68 | 6 | 65 |
| superbet | player_points_rebounds_assists | 162 | 4 | 162 |
| superbet | player_rebounds | 253 | 6 | 250 |
| superbet | player_rebounds_assists | 25 | 2 | 25 |
| superbet | player_steals | 38 | 4 | 38 |
| superbet | player_turnovers | 32 | 2 | 32 |
| volcanobet | game_total_ot | 553 | 67 | 527 |
| volcanobet | home_handicap_ot | 549 | 67 | 521 |
| volcanobet | player_points | 261 | 9 | 209 |

### Football

| Bookmaker | market type | raw | match buckets | odds |
| --- | --- | --- | --- | --- |
| 365 | football_double_chance | 469 | 126 | 373 |
| 365 | football_result | 474 | 126 | 378 |
| 365 | football_total_goals | 316 | 126 | 252 |
| admiralbet | football_double_chance | 592 | 172 | 507 |
| admiralbet | football_result | 615 | 176 | 528 |
| admiralbet | football_total_goals | 410 | 176 | 352 |
| balkanbet | football_double_chance | 524 | 68 | 199 |
| balkanbet | football_result | 534 | 68 | 204 |
| balkanbet | football_total_goals | 712 | 68 | 272 |
| betole | football_double_chance | 647 | 220 | 647 |
| betole | football_result | 660 | 220 | 660 |
| betole | football_total_goals | 440 | 220 | 440 |
| maxbet | football_double_chance | 563 | 172 | 507 |
| maxbet | football_result | 603 | 172 | 543 |
| maxbet | football_total_goals | 382 | 172 | 344 |
| merkurxtip | football_double_chance | 570 | 171 | 508 |
| merkurxtip | football_result | 579 | 171 | 513 |
| merkurxtip | football_total_goals | 386 | 171 | 342 |
| mozzart | football_double_chance | 488 | 106 | 308 |
| mozzart | football_result | 498 | 106 | 318 |
| mozzart | football_total_goals | 332 | 106 | 212 |
| oktagonbet | football_double_chance | 648 | 220 | 648 |
| oktagonbet | football_result | 660 | 220 | 660 |
| oktagonbet | football_total_goals | 440 | 220 | 440 |
| pinnbet | football_result | 498 | 157 | 471 |
| pinnbet | football_total_goals | 332 | 157 | 314 |
| soccerbet | football_double_chance | 557 | 182 | 536 |
| soccerbet | football_result | 567 | 182 | 546 |
| soccerbet | football_total_goals | 378 | 182 | 364 |
| superbet | football_double_chance | 810 | 158 | 466 |
| superbet | football_result | 825 | 158 | 474 |
| superbet | football_total_goals | 496 | 156 | 312 |
| volcanobet | football_double_chance | 580 | 129 | 378 |
| volcanobet | football_result | 627 | 133 | 399 |
| volcanobet | football_total_goals | 418 | 133 | 266 |

## Event resolution / matching

These counts come from the latest persisted snapshot's `resolved_event_members`, joined to `snapshot_matches`, so they are exact resolved-event counts rather than a lossy sum of market buckets.

| Sport | resolved events | match variants | member rows |
| --- | --- | --- | --- |
| basketball | 142 | 242 | 700 |
| football | 290 | 460 | 1889 |

### Event resolution by bookmaker

| Sport | Bookmaker | resolved events | match variants | member rows |
| --- | --- | --- | --- | --- |
| basketball | 365 | 56 | 56 | 56 |
| basketball | admiralbet | 62 | 63 | 63 |
| basketball | balkanbet | 30 | 32 | 32 |
| basketball | betole | 68 | 68 | 68 |
| basketball | maxbet | 58 | 58 | 58 |
| basketball | merkurxtip | 57 | 57 | 57 |
| basketball | mozzart | 54 | 54 | 54 |
| basketball | oktagonbet | 68 | 68 | 68 |
| basketball | pinnbet | 66 | 66 | 66 |
| basketball | soccerbet | 59 | 59 | 59 |
| basketball | superbet | 52 | 52 | 52 |
| basketball | volcanobet | 67 | 67 | 67 |
| football | 365 | 126 | 126 | 126 |
| football | admiralbet | 176 | 176 | 176 |
| football | balkanbet | 68 | 68 | 68 |
| football | betole | 220 | 220 | 220 |
| football | maxbet | 172 | 172 | 172 |
| football | merkurxtip | 171 | 171 | 171 |
| football | mozzart | 106 | 106 | 106 |
| football | oktagonbet | 220 | 220 | 220 |
| football | pinnbet | 157 | 157 | 157 |
| football | soccerbet | 182 | 182 | 182 |
| football | superbet | 158 | 158 | 158 |
| football | volcanobet | 133 | 133 | 133 |

## Investigation notes

- `365` football used `157` calls and took `194.317s`; total `365` scrape time was `264.521s` across basketball + football.
- `betole` football used `221` calls and took `222.458s`; total `betole` scrape time was `224.506s`.
- `superbet` football used `37` calls and took `46.361s`.
- `volcanobet` football used `20` calls and took `27.294s`.
- PinnBet football partial mode fetched `football_result` and `football_total_goals` only; no `football_double_chance` was expected or observed.
- For optimization, event resolution and outcome normalization are the first downstream areas to profile deeper. For scrape optimization, focus first on `365` and `betole` detail-fetch strategy.
