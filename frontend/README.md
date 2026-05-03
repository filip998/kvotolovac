# KvotoLovac Frontend

React/Vite interface for viewing canonical betting opportunities, odds history, and team/event review workflows.

## Tech stack

- React 19
- TypeScript 6
- Vite 8
- Tailwind CSS 4
- React Router 7
- TanStack Query 5
- Axios

## Quick start

From the repository root:

```bash
bash run-frontend.sh
```

The script installs dependencies when `frontend/node_modules` is missing and starts Vite at `http://localhost:5173`.

Manual equivalent:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Data modes

The app uses mock data by default:

```env
VITE_USE_MOCK=true
```

To connect to the backend:

```env
VITE_USE_MOCK=false
```

Then start the backend on `http://localhost:8000`. The Vite dev server proxies `/api` to that backend, and the Axios client uses `/api/v1` as its base URL.

Important: the mock flag is string-based. Only the exact value `false` disables mocks; missing `.env`, `true`, or any other value keeps mock data enabled.

## Commands

| Task | Command |
|---|---|
| Development server | `npm run dev` |
| Production build and TypeScript check | `npm run build` |
| Lint | `npm run lint` |
| Preview production build | `npm run preview` |

## Project structure

```text
src/
├── api/          # Axios client, React Query hooks, types, mock data
├── components/   # Reusable UI components
├── pages/        # Route pages
└── utils/        # Formatting, search, and constants
```

## Troubleshooting

| Symptom | Check |
|---|---|
| Real backend data does not appear | Set `VITE_USE_MOCK=false` in `frontend/.env` and restart Vite. |
| API requests fail in dev | Ensure the backend is running on port 8000; Vite proxies `/api` to that port. |
| Dependency install is skipped unexpectedly | Delete `frontend/node_modules` or run `npm install` manually. |
| TypeScript or lint errors block builds | Run `npm run build` and `npm run lint` from `frontend/` to reproduce them directly. |
