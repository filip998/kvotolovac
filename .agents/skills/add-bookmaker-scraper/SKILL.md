---
name: add-bookmaker-scraper
description: Add a new KvotoLovac bookmaker scraper end-to-end with worktree-first preflight, Chrome DevTools discovery, repo-specific integration, and verification.
---

Use this skill when the user wants to add, import, onboard, or scaffold a new bookmaker scraper for KvotoLovac.

Do not treat this as "just add one backend file." A real bookmaker addition in this repo crosses backend scraper code, bootstrap registration, mock mode, and frontend bookmaker metadata.

## Workflow

1. **Preflight first**
   - Run `git --no-pager status --porcelain` and `git rev-parse --abbrev-ref HEAD`.
   - If the checkout is dirty, stop and use `ask_user` to decide whether to commit, stash, or abort. Do **not** auto-stash or auto-commit.
   - If you are not already in a dedicated worktree, create one and continue there before touching code. Default branch/worktree naming:
     - branch: `bookmaker/<bookmaker-id>`
     - worktree path: `../kvotolovac-<bookmaker-id>`
   - After the worktree exists, do all reads, edits, tests, and commits from that worktree path.

2. **Collect the target inputs**
   - Use `ask_user` to gather:
     - bookmaker display name
     - canonical bookmaker ID slug
     - homepage URL
     - any known constraints (auth, geo-blocking, anti-bot, mobile-only endpoints)
     - whether a logo asset is available
   - Default the first pass to **basketball only** unless the user explicitly wants more.

3. **Survey the repo before coding**
   - Read the shared scraper surfaces:
     - `backend/app/scrapers/base.py`
     - `backend/app/scrapers/http_client.py`
     - `backend/app/main.py`
     - `backend/app/config.py`
     - `backend/app/scrapers/mock_scraper.py`
   - Read the best existing templates and pick the closest match:
     - `backend/app/scrapers/superbet_scraper.py` for event discovery + detail enrichment
     - `backend/app/scrapers/maxbet_scraper.py` for bulk-detail APIs
     - `backend/app/scrapers/mozzart_scraper.py` for split list/specials endpoints
   - Read at least one comparable test file, plus `backend/tests/test_superbet_scraper.py` for a richer reference.
   - Read the frontend/mock surfaces that can drift:
     - `frontend/src/api/mockData.ts`
     - `frontend/src/components/BookmakerBadge.tsx`
     - `frontend/src/pages/About.tsx`

4. **Discover the bookmaker using Chrome DevTools MCP first**
   - Use Chrome DevTools MCP as the default discovery tool for network requests, payloads, headers, pagination, and request timing.
   - Prefer stable JSON/API endpoints over brittle DOM scraping.
   - Capture representative request/response shapes and turn them into fixtures for tests.
   - If live discovery is blocked by auth, anti-bot, or hidden endpoints, use `ask_user` to request a HAR file or captured request/response payloads, then continue from that evidence.

5. **Implement the real scraper with repo patterns**
   - Add the real scraper under `backend/app/scrapers/`.
   - Reuse `HttpClient`; keep one client per bookmaker.
   - Register the scraper in `backend/app/main.py` and update default config only when the bookmaker is actually wired end-to-end.
   - Prefer extending existing patterns over inventing new abstractions.
   - Completion threshold for the first pass:
     - required: reliable basketball `player_points`
     - add `game_total` / `game_total_ot` when the source exposes them cleanly
     - add rebounds, assists, combo props, etc. only when they fit the same parser shape with low risk
   - Be deliberate about `start_time` semantics. Do not blindly shift or reformat timestamps across layers.
   - Handle league naming with existing patterns (`league_registry` and per-scraper canonical mappings). Do not create a parallel normalization system.

6. **Do not leave a partial integration**
   - Keep mock mode working:
     - `backend/app/scrapers/mock_scraper.py`
     - `frontend/src/api/mockData.ts`
   - Update hardcoded bookmaker metadata surfaces when needed:
     - `frontend/src/components/BookmakerBadge.tsx`
     - `frontend/src/pages/About.tsx`
     - `frontend/public/bookmaker-logos/*` if a logo exists; otherwise rely on the badge initials fallback
   - Search the repo for the new bookmaker ID/name before finishing so you do not miss any hardcoded lists or maps.

7. **Verify before presenting**
   - Run targeted scraper tests first.
   - Always run the backend suite with the repo's real command:
     - `cd backend && ./venv/bin/pytest -q`
   - If frontend surfaces changed, run:
     - `cd frontend && npm run build`
     - `cd frontend && npm run lint`
   - If the only validation available is static, add a small smoke path instead of claiming success with no runtime signal.

8. **Finalize cleanly**
   - Summarize what changed, including every integration surface touched.
   - Call out any limitations explicitly (for example: only player points in first pass, missing logo asset, blocked markets).
   - Commit the result in the dedicated worktree after verification passes.

## Non-negotiables

- No worktree, no implementation.
- No silent fallback from blocked discovery: ask for HAR or captured payloads.
- No partial bookmaker additions that skip mock mode or hardcoded frontend metadata.
- No unnecessary new abstractions when an existing scraper pattern already fits.
