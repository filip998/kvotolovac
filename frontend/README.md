# KvotoLovac 🎯 — Frontend

Odds comparison tool for Serbian bookmakers that detects discrepancies in basketball betting lines.

## Tech Stack

- React 18 + TypeScript + Vite
- TailwindCSS (dark theme)
- React Router
- TanStack Query (React Query)
- Axios

## Quick Start

```bash
npm install
npm run dev
```

The app runs with **mock data** by default (`VITE_USE_MOCK=true`).

## Connecting to Backend

Set `VITE_USE_MOCK=false` in `.env` and ensure the backend is running on `http://localhost:8000`. The Vite dev server proxies `/api` requests automatically.

## Build

```bash
npm run build
```

## Project Structure

```
src/
├── api/          # API client, React Query hooks, types, mock data
├── components/   # Reusable UI components
├── pages/        # Route pages (Dashboard, MatchDetail, About)
└── utils/        # Formatting helpers, constants
```
