# KvotoLovac Backend

Odds comparison tool for Serbian bookmakers — detects discrepancies in basketball betting lines.

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs`

## Running Tests

```bash
python -m pytest tests/ -v
```

## Configuration

Copy `.env.example` to `.env` and adjust:

| Variable | Default | Description |
|---|---|---|
| DATABASE_URL | sqlite:///./kvotolovac.db | SQLite database path |
| SCRAPE_INTERVAL_MINUTES | 10 | How often to scrape |
| LOG_LEVEL | INFO | Logging level |
| CORS_ORIGINS | * | Allowed CORS origins |
| BOOKMAKERS | mozzart,meridian,maxbet | Active bookmakers |
| SCRAPER_MODE | mock | `mock` for fixtures, `real` for live bookmaker scrapers |
| RATE_LIMIT_PER_SECOND | 1.0 | Per-real-scraper request rate limit |
| MERIDIAN_RATE_LIMIT_PER_SECOND | 2.0 | Meridian-only request rate override for its event/market fan-out |
| PROXY_LIST | (empty) | Comma-separated proxy URLs applied to each real scraper client |

## Scrape throughput model

- The scheduler runs scraper tasks concurrently at the top level, so slow bookmakers do not stall the whole scrape phase.
- In `real` mode, each scraper gets its own `HttpClient`, so HTTP rate limiting is isolated per bookmaker instead of being shared globally across all bookmakers.
- Meridian now filters obvious non-candidate events and fans out market requests with bounded concurrency; use `MERIDIAN_RATE_LIMIT_PER_SECOND` if Meridian needs a higher cap without changing other bookmakers.
- The API now starts immediately and the initial scrape runs in the scheduler background loop instead of blocking app startup.
- `GET /api/v1/status` includes live scan progress metadata while a cycle is running, so the frontend can show warmup/progress state instead of timing out on first load.
- `POST /api/v1/scrape/trigger` now rejects with `409` while a scan is already running, so callers do not queue duplicate full cycles behind the background scheduler.
- Normalization, storage, analysis, and notifications still run after scraping with the same downstream behavior as before.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /api/v1/discrepancies | List active discrepancies |
| GET | /api/v1/discrepancies/{id} | Single discrepancy detail |
| GET | /api/v1/matches | List matches by league |
| GET | /api/v1/matches/{id} | Match detail |
| GET | /api/v1/matches/{id}/odds | All odds for a match |
| GET | /api/v1/matches/{id}/history | Historical odds movement |
| GET | /api/v1/leagues | List leagues |
| GET | /api/v1/bookmakers | List bookmakers |
| GET | /api/v1/status | System health/status |
| POST | /api/v1/scrape/trigger | Manually trigger scrape |
