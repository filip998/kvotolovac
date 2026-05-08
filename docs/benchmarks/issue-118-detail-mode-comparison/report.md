# Issue 118 detail-mode comparison

This report compares BetOle and PinnBet partial vs full detail mode using the existing backend benchmark recorder. Both runs used real scrapers, `BOOKMAKERS=betole,pinnbet`, separate SQLite databases, and separate benchmark directories.

## Command template

```bash
SCRAPER_MODE=real \
BOOKMAKERS=betole,pinnbet \
BETOLE_DETAIL_MODE=partial \
PINNBET_DETAIL_MODE=partial \
BENCHMARK_DIR=/tmp/kvotolovac-issue-118-benchmark-partial \
DATABASE_URL=sqlite:////tmp/kvotolovac-issue-118-benchmark-partial/kvotolovac.db \
AUTO_MIGRATE_ON_STARTUP=true \
./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For full mode, change both detail-mode environment variables to `full` and use a separate `BENCHMARK_DIR`/`DATABASE_URL`. If full mode is ever run operationally, prefer bookmaker/scrape-type caps such as `betole:outcome_offer:full:<rate>` and `pinnbet:outcome_offer:full:<rate>` instead of raising global rate limits.

## Summary

| Mode | Scrape | Cycle | Opportunities | Failure rate |
|---|---:|---:|---:|---:|
| Partial | 2.530s | 6.393s | 0 | 0.0% |
| Full | 311.095s | 315.020s | 0 | 0.0% |

Full mode added football double-chance rows, but it did not produce any active opportunities in this targeted run. The added coverage therefore did not justify the large request and rate-limit-wait increase.

## Per-bookmaker cost

| Mode | Bookmaker | Duration | Logical requests | Rate-limit wait | Raw rows | Normalized matches | Normalized odds/offers |
|---|---|---:|---:|---:|---:|---:|---:|
| Partial | BetOle | 3.032s | 3 | 4.162s | 1,735 | 255 | 1,081 |
| Partial | PinnBet | 4.372s | 3 | 3.715s | 2,424 | 255 | 2,347 |
| Full | BetOle | 313.546s | 311 | 590.256s | 2,641 | 255 | 1,605 |
| Full | PinnBet | 188.363s | 186 | 362.420s | 2,966 | 255 | 2,871 |

## Football market coverage

| Mode | Bookmaker | football_result | football_total_goals | football_double_chance |
|---|---|---:|---:|---:|
| Partial | BetOle | 531 | 354 | 0 |
| Partial | PinnBet | 531 | 354 | 0 |
| Full | BetOle | 531 | 354 | 524 |
| Full | PinnBet | 531 | 354 | 524 |

## Detail-mode opportunity yield

| Mode | Bookmaker | Detail mode | Opportunities involving bookmaker | Opportunity legs | Market counts |
|---|---|---|---:|---:|---|
| Partial | BetOle | partial | 0 | 0 | `{}` |
| Partial | PinnBet | partial | 0 | 0 | `{}` |
| Full | BetOle | full | 0 | 0 | `{}` |
| Full | PinnBet | full | 0 | 0 | `{}` |

## Decision

Keep `BETOLE_DETAIL_MODE=partial` and `PINNBET_DETAIL_MODE=partial` as defaults. Full mode is measurable through benchmark output now, but this run shows it adds 1,048 double-chance rows at very high request cost and zero opportunity yield.
