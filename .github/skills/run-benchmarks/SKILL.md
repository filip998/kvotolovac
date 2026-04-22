---
name: run-benchmarks
description: Run KvotoLovac scraper benchmarks, summarize the results, compare cycles, and investigate what got slower using the existing backend benchmark pipeline.
---

Use this skill when the user wants to benchmark KvotoLovac's backend scrape cycle, compare benchmark runs over time, explain the latest numbers, or investigate why a cycle or scraper got slower.

Do not build a parallel benchmark harness first. This repo already records scrape-cycle benchmarks through the backend scheduler and exposes them through both API responses and on-disk artifacts.

## Workflow

1. **Clarify the benchmark scope first**
   - Use `ask_user` if the request does not already specify:
     - `mock` vs `real` mode
     - all bookmakers vs a targeted subset
     - latest-cycle summary vs historical comparison
     - whether the goal is code-path regression detection or live bookmaker/network timing
   - Default to `mock` when the user wants deterministic pipeline regression checks.
   - Use `real` only when the user explicitly wants live scraper/network behavior; call out that these runs are noisier because bookmaker latency, payload size, and rate limiting vary.

2. **Reuse the existing backend surfaces**
   - The benchmark entrypoints already exist:
     - `POST /api/v1/scrape/trigger` runs one scrape cycle immediately.
     - `GET /api/v1/scraper-benchmarks` returns the latest benchmark snapshot.
     - `settings.benchmark_dir/cycle-*.json` stores per-cycle snapshots.
     - `settings.benchmark_dir/cycles.ndjson` stores append-only history for comparison.
   - Do not edit `.env` just to run a temporary benchmark. Prefer env-prefixed commands for one-off runs.
   - If a backend server is already running, inspect it first instead of restarting it. In this shared environment, do not kill an unknown process without user approval.
   - Resolve the active benchmark directory from backend config or environment before reading files. `backend/benchmarks` is only the default path when `benchmark_dir` has not been overridden.

3. **Bring up the backend safely**
   - First check whether the backend is already responding at `http://localhost:8000/api/v1/status`.
   - If it is not running and the default config is acceptable, start it with:
     - `bash run-backend.sh`
   - If you need a one-off benchmark configuration, start the backend with temporary env overrides instead of mutating repo config, for example:
     - `SCRAPER_MODE=real BOOKMAKERS=meridian,mozzart bash run-backend.sh`
   - Prefer a targeted bookmaker subset when the user is chasing one suspected slowdown. Do not benchmark every bookmaker by default if the question is narrower.

4. **Trigger a reproducible benchmark capture**
   - Check `/api/v1/status` before triggering.
   - If `scan.in_progress` is already true, wait for that cycle to finish or explain that `POST /api/v1/scrape/trigger` will return `409` while a cycle is active.
   - For reproducible measurements, trigger an explicit cycle with `POST /api/v1/scrape/trigger` instead of waiting for the background interval.
   - Immediately fetch `GET /api/v1/scraper-benchmarks`.
   - Read the newest `cycle-*.json` and the tail of `cycles.ndjson` from the active benchmark directory so you have both the latest snapshot and recent history.

5. **Report benchmark results with derived metrics**
   - Always report these top-level numbers:
     - `scrape_duration_ms`
     - `cycle_duration_ms`
     - `cycle_overhead_ms = cycle_duration_ms - scrape_duration_ms`
     - `total_raw_items`
     - `total_matches`
     - `total_odds`
   - Rank scrapers by `duration_ms` descending.
   - Compute zero-safe efficiency signals per scraper:
     - `ms_per_raw_item`
     - `ms_per_match`
     - `ms_per_odd`
   - Include:
     - `leagues_attempted`
     - `leagues_failed`
     - `failure_rate`
   - Separate facts from inference. Example:
     - fact: `meridian` was 41% of total scrape time
     - inference: likely network or payload-size slowdown

6. **Compare cycles carefully**
   - Use `cycles.ndjson` from the active benchmark directory for historical comparison.
   - Prefer comparing the latest cycle against the median of a few recent comparable cycles instead of a single older run.
   - Only treat runs as comparable when the mode, bookmaker set, and network conditions are known to be similar.
   - The current benchmark snapshot does **not** persist config metadata like `SCRAPER_MODE`, proxies, or bookmaker subset. If that context is unknown, say the comparison is noisy rather than pretending it is apples-to-apples.

7. **Investigate what got slower and why**
   - If one bookmaker's `duration_ms` increased and `raw_items` or `leagues_attempted` increased proportionally, treat it as likely workload growth before calling it a regression.
   - If `duration_ms` increased while workload stayed flat, suspect network behavior, rate limiting, bookmaker payload changes, or extra detail-enrichment work in that scraper.
   - If `failure_rate` rose, inspect logs and treat retries, timeouts, or repeated scraper failures as likely contributors.
   - If `scrape_duration_ms` stays flat but `cycle_duration_ms` rises, the slowdown is downstream of scraping. Use scheduler context to frame that correctly:
     - scraping is benchmarked per bookmaker
     - normalization, storing, discrepancy analysis, and notifications are only visible inside the overall cycle time
     - retention cleanup and post-cycle canonical merges happen after the benchmark snapshot is published, so they are **not** included in `cycle_duration_ms`
   - Do **not** claim you proved which downstream phase is slow from the current snapshot alone. If exact attribution is required, recommend adding phase-level timers instead of guessing.
   - When investigating a suspicious regression, compare mock-mode and real-mode runs if possible:
     - mock slower too: likely shared pipeline or data-shape regression
     - only real slower: likely real-scraper-specific work or external effects
   - Do **not** overclaim what mock mode proves. Mock mode swaps in `MockScraper`, so it can help separate shared pipeline regressions from real-scraper-specific/external effects, but it cannot distinguish network latency from logic regressions inside a real scraper.

8. **Verify and preserve evidence**
   - If you changed code while investigating, run the benchmark-related verification surfaces:
     - `cd backend && ./venv/bin/pytest -q tests/test_scraper_benchmarks.py tests/test_api.py -k 'trigger_scrape or benchmarks'`
   - If you only ran benchmarks, preserve the evidence you used:
     - trigger response
     - latest API snapshot
     - snapshot file path
     - comparison summary from `cycles.ndjson`

9. **Present the result cleanly**
   - State:
     - how the benchmark was run
     - which assumptions made the comparison valid or noisy
     - the latest cycle summary
     - the slowest scraper(s) or the downstream-overhead finding
     - the most likely explanation, clearly labeled as inference when not proven
     - what additional instrumentation would be needed if the current data cannot prove the cause

## Non-negotiables

- No new benchmark system before using the existing recorder, API, and files.
- No relying on the scheduler interval when the user wants a reproducible benchmark; trigger an explicit cycle.
- No apples-to-oranges comparisons across different modes, bookmaker sets, or network conditions without saying so.
- No fake certainty about downstream phase attribution when the recorder only exposes scrape-level and total-cycle timings.
