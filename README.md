# KvotoLovac

KvotoLovac is an odds-comparison app for Serbian bookmakers. It has a FastAPI backend that scrapes bookmaker feeds, normalizes events and teams, stores current odds in SQLite, and detects canonical betting opportunities. The React/Vite frontend shows those opportunities, odds history, and review workflows.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI app, scraper scheduler, normalization pipeline, SQLite store, pytest suite, registry JSON files |
| `frontend/` | React 19 + TypeScript + Vite app with TanStack Query and Tailwind CSS |
| `run-backend.sh` | Creates `backend/venv` if needed, installs Python dependencies, starts uvicorn on port 8000 |
| `run-frontend.sh` | Installs frontend dependencies if needed, starts Vite on port 5173 |
| `run-all.sh` | Starts backend and frontend together |

## Quick start

From the repository root:

```bash
bash run-all.sh
```

The helper scripts install missing dependencies automatically:

- backend dependencies go into `backend/venv`;
- frontend dependencies go into `frontend/node_modules`;
- the backend serves API routes at `http://localhost:8000/api/v1`;
- the frontend serves the app at `http://localhost:5173`.

The frontend uses mock data by default. To connect it to the backend, copy `frontend/.env.example` to `frontend/.env` and set `VITE_USE_MOCK=false`; Vite proxies `/api` requests to `http://localhost:8000`.

## Manual setup

Backend:

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Runtime modes

| Mode | Configuration | Use case |
|---|---|---|
| Frontend mock data | `VITE_USE_MOCK=true` or no frontend `.env` | UI development without a backend |
| Frontend backed by API | `VITE_USE_MOCK=false` and backend on port 8000 | End-to-end local development |
| Backend mock scrapers | `SCRAPER_MODE=mock` | Deterministic local backend development |
| Backend real scrapers | `SCRAPER_MODE=real` | Live bookmaker scraping with rate limits/proxies as needed |

Backend configuration lives in `backend/.env`; see `backend/.env.example` and `backend/README.md` for the full runtime settings reference.

## Development commands

| Task | Command |
|---|---|
| Backend tests | `cd backend && ./venv/bin/pytest -q` |
| Backend dev server | `bash run-backend.sh` |
| Frontend dev server | `bash run-frontend.sh` |
| Frontend build/type check | `cd frontend && npm run build` |
| Frontend lint | `cd frontend && npm run lint` |

## Architecture overview

The backend scheduler runs scrape cycles in the background:

1. bookmaker scrapers fetch threshold odds and/or outcome offers;
2. normalizers resolve teams, leagues, events, and markets;
3. compatibility tables keep current odds/history and outcome offers available for existing APIs;
4. canonical offer adapters feed one opportunity analyzer;
5. active opportunities and notifications are persisted for the frontend.

SQLite is the local data store by default (`backend/kvotolovac.db`). The scheduler starts automatically with the FastAPI app, and `POST /api/v1/scrape/trigger` can start a manual cycle when no cycle is already running.

## Troubleshooting

| Symptom | Check |
|---|---|
| Frontend shows only mock data | Set `VITE_USE_MOCK=false` in `frontend/.env` and restart Vite |
| Frontend API calls fail | Ensure the backend is running on `http://localhost:8000`; Vite proxies `/api` to that port |
| Backend starts with old data | Check `DATABASE_URL`; the default database is `backend/kvotolovac.db` when running from `backend/` |
| Scrapers do not hit live sites | Set `SCRAPER_MODE=real`; the default is `mock` |
| Live bookmaker endpoints are slow or blocked | Tune `RATE_LIMIT_PER_SECOND`, bookmaker-specific detail modes, and `PROXY_LIST`; some upstream sites may block requests |
| Port already in use | Stop the process using port 8000 for backend or 5173 for frontend, or run uvicorn/Vite manually with a different port |

See `backend/README.md` and `frontend/README.md` for service-specific details.
