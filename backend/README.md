# KvotoLovac Backend

FastAPI service for scraping Serbian bookmaker feeds, normalizing teams/events, storing odds in SQLite, and detecting canonical betting opportunities.

## Quick start

From the repository root:

```bash
bash run-backend.sh
```

The script creates `backend/venv` when needed, installs `requirements.txt`, and starts uvicorn at `http://localhost:8000`.

Manual equivalent:

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API docs are available at `http://localhost:8000/docs`.

## Tests

Always run tests through the backend virtual environment:

```bash
cd backend
./venv/bin/pytest -q
```

## Database migrations

Schema creation and upgrades are managed by Alembic migrations under
`backend/app/migrations/`. Backend startup does **not** run migrations
automatically; it verifies that the configured database is already at the latest
revision and fails with an actionable error if it is not.

Create or upgrade the configured local database before starting the backend:

```bash
cd backend
./venv/bin/alembic upgrade head
```

`alembic` reads `DATABASE_URL` the same way the backend does, defaulting to
`sqlite:///./kvotolovac.db`. To migrate a different SQLite file, set
`DATABASE_URL` for the command:

```bash
cd backend
DATABASE_URL=sqlite:////absolute/path/to/kvotolovac.db ./venv/bin/alembic upgrade head
```

Create a new migration revision for future schema changes:

```bash
cd backend
./venv/bin/alembic revision -m "describe schema change"
```

Use raw SQL / `op.execute()` in migration files. Do not add ongoing schema
compatibility blocks to `app/database.py`; add versioned migration revisions and
fixture-backed upgrade tests instead.

## Configuration

Settings are loaded from environment variables and `backend/.env`. Copy `backend/.env.example` to `backend/.env` for local overrides.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./kvotolovac.db` | SQLite database URL. Relative paths resolve from the backend working directory. |
| `SCRAPE_INTERVAL_MINUTES` | `10` | Background scheduler interval between scrape cycles. |
| `SCRAPE_LOOKAHEAD_HOURS` | `24` | Event lookahead window used by scrapers that support date filtering. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `CORS_ORIGINS` | `*` | Comma-separated CORS origins. |
| `BOOKMAKERS` | `mozzart,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet` | Comma-separated bookmaker IDs enabled for scrape cycles. Meridian is available in code but excluded from the default list because its market-detail endpoint is often blocked upstream. |
| `ENABLED_SPORTS` | `basketball` | Comma-separated sports enabled in the canonical offer pipeline. |
| `SCRAPER_MODE` | `mock` | `mock` uses deterministic local scraper data; `real` calls live bookmaker endpoints. |
| `PROXY_LIST` | empty | Comma-separated proxy URLs distributed to real scraper HTTP clients. |
| `RATE_LIMIT_PER_SECOND` | `1.0` | Default per-scraper HTTP rate limit in real mode. |
| `MERIDIAN_RATE_LIMIT_PER_SECOND` | `2.0` | Meridian-specific HTTP rate limit override. |
| `BOOKMAKER_RATE_LIMITS` | empty | Optional comma/semicolon-separated caps in `<bookmaker>:<rate>` form, e.g. `betole:0.5,365:1`. Caps never raise a scraper above its default global/Meridian rate. |
| `SCRAPE_TYPE_RATE_LIMITS` | empty | Optional comma/semicolon-separated caps in `<bookmaker>:<lane>:<rate>` or `<bookmaker>:<lane>:<detail_mode>:<rate>` form, e.g. `betole:outcome_offer:full:0.5`. |
| `SOCCERBET_DETAIL_MODE` | `partial` | `partial` uses broad preview feeds; `full` adds match-by-code enrichment. |
| `MERKURXTIP_DETAIL_MODE` | `partial` | `partial` uses list feeds; `full` adds match detail for alternate totals. |
| `PINNBET_DETAIL_MODE` | `partial` | `partial` uses football list data; `full` adds per-event football detail. |
| `BETOLE_DETAIL_MODE` | `partial` | `partial` uses football list data; `full` adds per-event football detail for double chance. |
| `NOTIFICATION_GAP_THRESHOLD` | `1.5` | Minimum opportunity gap/margin threshold used by notification logic. |
| `PERSIST_INAPP_NOTIFICATIONS` | `false` | Persist generated in-app notifications when enabled. |
| `NOTIFICATION_RETENTION_DAYS` | `3` | Retention window for persisted notifications. |
| `ODDS_HISTORY_RETENTION_DAYS` | `7` | Retention window for historical odds rows. |
| `TEAM_REVIEW_RETENTION_DAYS` | `90` | Retention window for team review cases. |
| `BENCHMARK_DIR` | `backend/benchmarks` | Directory for scraper benchmark JSON artifacts. Ignored by git. |
| `LEAGUE_REGISTRY_PATH` | `backend/app/data/league_registry.json` | League registry JSON path. |
| `TEAM_REGISTRY_PATH` | `backend/app/data/team_registry.json` | Legacy team alias registry JSON path used during registry bootstrap. |

## Runtime modes

### Mock scraper mode

```env
SCRAPER_MODE=mock
```

Use this for local backend development and demos. It avoids live bookmaker requests and keeps scrape behavior deterministic.

### Real scraper mode

```env
SCRAPER_MODE=real
RATE_LIMIT_PER_SECOND=1.0
PROXY_LIST=
```

Use this only when you intend to call live bookmaker endpoints. Each bookmaker has its own HTTP client, so rate limits are isolated per bookmaker. Some upstream sites may block or throttle requests; use conservative rate limits and proxies where appropriate.

### Sport and bookmaker selection

`BOOKMAKERS` controls which scraper IDs are scheduled. `ENABLED_SPORTS` controls which supported sports enter the canonical offer pipeline. Both values are comma-separated lists.

## Scrape throughput model

- The scheduler runs scraper tasks concurrently at the top level, so slow bookmakers do not stall the whole scrape phase.
- In `real` mode, each scraper gets its own `HttpClient`, so HTTP rate limiting is isolated per bookmaker instead of shared globally.
- Meridian is temporarily excluded from the default `BOOKMAKERS` list because its market-detail endpoint is often blocked by upstream Cloudflare protection. It can still be explicitly enabled with `BOOKMAKERS=...meridian...`; use `MERIDIAN_RATE_LIMIT_PER_SECOND` if Meridian needs a higher cap without changing other bookmakers.
- `BOOKMAKER_RATE_LIMITS` and `SCRAPE_TYPE_RATE_LIMITS` are backend-only caps for isolating expensive paths without raising defaults. Scrape-type caps take precedence over bookmaker caps; detail-specific caps such as `betole:outcome_offer:full:0.5` apply only when that scraper is in the matching detail mode.
- The API starts immediately and the initial scrape runs in the scheduler background loop instead of blocking app startup.
- `GET /api/v1/status` includes live scan progress metadata while a cycle is running, so the frontend can show warmup/progress state instead of timing out on first load.
- `POST /api/v1/scrape/trigger` rejects with `409` while a scan is already running, so callers do not queue duplicate full cycles behind the background scheduler.

## Unified offer pipeline

The scheduler discovers scraper work through explicit capabilities rather than hardcoded basketball/football branches:

- `threshold_odds` capabilities scrape existing over/under rows into `RawOddsData` and persist them in `odds`/`odds_history` for compatibility with match odds and history APIs.
- `outcome_offer` capabilities scrape one-outcome rows into `RawOutcomeOffer` and persist them in `outcome_offers` for compatibility with `/api/v1/market-offers`.
- Both lanes normalize into current snapshot rows, then feed the same canonical offer adapter and `analyze_canonical_offers()` pass. `opportunities` is the primary public analysis output.

The compatibility tables remain intentionally separate in this phase. Explicit migration tooling or a durable canonical-offer table belongs to the dedicated schema evolution work in issue #47.

## API endpoints

All routes are mounted under `/api/v1`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/opportunities` | List active canonical opportunities. |
| `GET` | `/market-offers` | List current one-outcome offers. |
| `GET` | `/matches` | List matches by league. |
| `GET` | `/matches/{id}` | Match detail. |
| `GET` | `/matches/{id}/odds` | Current odds for a match. |
| `GET` | `/matches/{id}/history` | Historical odds movement for a match. |
| `POST` | `/matches/merge` | Merge two normalized matches. |
| `GET` | `/leagues` | List leagues. |
| `GET` | `/bookmakers` | List bookmakers. |
| `GET` | `/status` | System health and scheduler status. |
| `POST` | `/scrape/trigger` | Manually trigger a scrape cycle. |
| `GET` | `/canonical-teams` | List canonical teams. |
| `POST` | `/canonical-teams/{team_id}/merge` | Merge a canonical team into another team. |
| `POST` | `/canonical-teams/{team_id}/unmerge` | Unmerge a previously merged team. |
| `GET` | `/team-review/cases` | List team review cases. |
| `POST` | `/team-review/cases/{case_id}/approve` | Approve a team review case. |
| `POST` | `/team-review/cases/{case_id}/decline` | Decline a team review case. |
| `GET` | `/event-review/cases` | List event review cases. |
| `POST` | `/event-review/cases/{case_id}/accept` | Accept an event review case. |
| `POST` | `/event-review/cases/{case_id}/decline` | Decline an event review case. |
| `POST` | `/event-review/merge` | Merge canonical events. |
| `GET` | `/unresolved-odds` | List odds rows that could not be normalized. |
| `GET` | `/scraper-benchmarks` | Read the latest scraper benchmark cycle. |

## Troubleshooting

| Symptom | Check |
|---|---|
| Backend uses mock data | Set `SCRAPER_MODE=real` in `backend/.env` and restart the server. |
| Backend cannot find the database | Check `DATABASE_URL`; with the helper script, `sqlite:///./kvotolovac.db` points at `backend/kvotolovac.db`. |
| Frontend cannot reach API | Start the backend on port 8000; the frontend dev server proxies `/api` to that port. |
| Manual scrape returns `409` | A scheduler cycle is already running; wait for `GET /api/v1/status` to report idle. |
| Live scraper requests are slow or blocked | Lower `RATE_LIMIT_PER_SECOND`, configure `PROXY_LIST`, or disable problematic bookmaker IDs in `BOOKMAKERS`. |
| Benchmark files appear locally | `BENCHMARK_DIR` defaults to `backend/benchmarks`, which is ignored by git. |
