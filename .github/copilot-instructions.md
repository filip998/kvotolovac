# KvotoLovac — Copilot Instructions

Odds-comparison tool for Serbian basketball bookmakers. Two services in one repo: a Python/FastAPI backend that scrapes bookmakers and detects line discrepancies, and a React/Vite frontend that visualizes them.

## Layout

- `backend/` — FastAPI app (`app/`), pytest suite (`tests/`), SQLite store (`kvotolovac.db`), JSON registries (`app/data/`).
- `frontend/` — React 19 + TypeScript + Vite + TanStack Query + Tailwind v4.
- `run-backend.sh`, `run-frontend.sh`, `run-all.sh` — dev launchers (create venv / install deps as needed).

## Build, lint, test

Always invoke the backend through its venv — system `pytest` may resolve to a different Python than the running server.

| Task | Command |
|---|---|
| Backend test suite | `cd backend && ./venv/bin/pytest -q` |
| Backend single test | `cd backend && ./venv/bin/pytest tests/test_match_merge.py::test_merge_matches_happy_path -q` |
| Backend dev server | `bash run-backend.sh` (uvicorn on :8000) |
| Frontend build (also TS check) | `cd frontend && npm run build` |
| Frontend lint | `cd frontend && npm run lint` |
| Frontend single-file lint | `cd frontend && npx eslint src/components/Foo.tsx` |
| Frontend type-check only | `cd frontend && npx tsc -p tsconfig.app.json --noEmit` |
| Frontend dev server | `bash run-frontend.sh` (vite on :5173, proxies `/api` to :8000) |

There are 5 known pre-existing scraper failures in `test_meridian_scraper.py`, `test_oktagonbet_scraper.py`, `test_pinnbet_scraper.py` (asyncio "no current event loop"). They are not your fault — don't try to fix them as part of unrelated work, but don't let your changes add to the count.

## Backend architecture (the important bits)

Scrape → normalize → store → analyze → notify, orchestrated by a single async scheduler.

- **Scrapers** (`app/scrapers/`) implement `BaseScraper`, registered in `app/scrapers/registry.py`. Each bookmaker has its own `HttpClient` so per-bookmaker rate limits don't interfere. `SCRAPER_MODE=mock` swaps in `MockScraper` for tests/dev; `=real` uses live HTTP.
- **Scheduler** (`app/services/scheduler.py`) runs cycles every `SCRAPE_INTERVAL_MINUTES`. Use `scheduler.is_cycle_in_progress` to gate destructive endpoints (return 409 if true) — don't acquire your own lock.
- **Normalizer** (`app/services/normalizer.py`) computes the deterministic `match_id = md5(f"{sport}:{start_time}:{home_id}:{away_id}").hexdigest()[:12]`. Two matches across bookmakers collapse iff their canonical home/away team IDs **and** start_time match exactly. Time strings are compared as-is — don't reformat them.
- **Team registry** (`app/services/team_registry.py`) is the single source of truth for canonical teams and aliases. It bootstraps legacy `team_registry.json` aliases into `canonical_teams`/`team_aliases` on first use. Use `merge_canonical_teams()` to fold one team into another (also reassigns aliases and pending review cases).
- **Store** (`app/store/odds_store.py`) is the only module that touches sqlite directly. `odds` has `UNIQUE(match_id, bookmaker_id, market_type, player_name, threshold)` — note `scraped_at` is **not** part of the key, so any operation that reassigns `match_id` must dedupe on this tuple across all involved matches before the UPDATE or it will trip the constraint. `odds_history` and `discrepancies` have no UNIQUE constraint.
- **API routers** are mounted under `/api/v1` via `app/api/router.py`. Add new routers there.
- **Schemas** (`app/models/schemas.py`) are Pydantic and shared between API + store. If you add fields to a response model, also extend the corresponding TS type in `frontend/src/api/types.ts`.

## Frontend architecture

- **Mock-first**: `frontend/src/api/hooks.ts` reads `VITE_USE_MOCK` (default `true`). Every hook has a mock branch backed by `src/api/mockData.ts` and a real branch using `axios`. When you add a new mutation/query, implement **both** branches or the dev experience breaks.
- **Query keys** are flat strings: `['matches']`, `['discrepancies']`, `['canonicalTeams']`, `['teamReviewCases']`. After mutations that affect multiple resources (e.g. match merges that also touch teams), invalidate all relevant keys.
- **Routing**: `App.tsx` uses React Router v7. Pages live in `src/pages/`, reusable UI in `src/components/`.
- **Styling**: Tailwind v4 via `@tailwindcss/vite`. Tokens used across the app: `bg`, `surface`, `border`, `border-hover`, `text`, `text-muted`, `text-secondary`, `accent`, `danger`, `warning`. Reuse them — don't introduce raw hex colors.
- **Time formatting**: always use `frontend/src/utils/format.ts::formatDateTime`. It renders in `Europe/Belgrade`, not the viewer's TZ.
- **Search**: use `frontend/src/utils/search.ts` (`buildSearchIndex` + `filterSearchIndex` + `normalizeSearchText`) for any new client-side filter — it handles diacritics consistently.

## Conventions you'll trip over

- **Don't reformat ISO datetimes** between layers. The match_id hash is sensitive to whitespace and offset format.
- **Reversed-name matching** in the normalizer relies on raw surface abbreviation cues (`J.`, `A.J.`, `VJ`), not token length. If you change name canonicalization, run `tests/test_normalizer.py` — there are explicit regressions for swapped full names.
- **AdmiralBet basketball** dateTimes are treated as UTC wall-clock — do not shift them from Europe/Belgrade. See `app/scrapers/admiralbet_scraper.py`.
- **Dashboard total stake** is centralized in `useDashboardStakeUnits` (localStorage key `kvotolovac-dashboard-stake-units`). Don't read/write the key directly.
- **Effects in React 19**: this repo uses `eslint-plugin-react-hooks` v7 which forbids `setState` directly inside `useEffect`. Derive state during render instead.

## When adding a destructive backend endpoint

Pattern established by `POST /matches/merge`:
1. Return 409 if `scheduler.is_cycle_in_progress`.
2. Validate everything (existence, cross-field constraints) before the first mutation.
3. Run the DB transaction (`BEGIN IMMEDIATE` / `commit` / `rollback`) inside `odds_store`.
4. If the operation also persists registry-level changes (team aliases, league mappings), do them **after** the DB transaction commits, and surface partial failures with a 500 + actionable detail rather than rolling back the successful work.

## Configuration

Backend reads `.env` via `app/config.py`. Useful knobs: `SCRAPER_MODE` (`mock`/`real`), `SCRAPE_INTERVAL_MINUTES`, `BOOKMAKERS`, `RATE_LIMIT_PER_SECOND`, `MERIDIAN_RATE_LIMIT_PER_SECOND`, `PROXY_LIST`, `CORS_ORIGINS`. Frontend reads `VITE_USE_MOCK` (string, compared to `'false'`).
