# ProoFix Frontend

AI execution workspace for **ProoFix**, the autonomous repository repair system. The UI makes agent reasoning observable: what each agent investigated, what it found, what evidence it produced, and why the run ended in an auto-merge, diff-only, or draft PR.

It is deliberately **not** a chatbot, an analytics dashboard, or a DevOps monitoring panel — it reads as an execution journal.

## Landing page

The deployed home page (`/`) uses `Landing_page/src/`; the execution workspace is at `/workspace`. The older landing components remain in `src/components/landing/`, and the old standalone preview and its Playwright tests remain in `Landing/`. The new landing folder uses the main frontend build and dependencies.

Keep the Vercel Root Directory set to `frontend` and build with `npm run build`. The frontend build includes the home page; no separate landing build is needed. For standalone preview development, run commands from `frontend/Landing/`.

## Stack

| Concern    | Choice                                                   |
| ---------- | -------------------------------------------------------- |
| Framework  | React 19 + TanStack Start (SSR)                          |
| Routing    | TanStack Router (file-based, `src/routes/`)              |
| Data       | TanStack Query                                           |
| Styling    | Tailwind CSS v4                                          |
| Primitives | Radix UI + shadcn-style wrappers in `src/components/ui/` |
| Build      | Vite 8                                                   |

## Getting started

```bash
npm install
npm run dev          # http://localhost:5173
```

Other scripts: `npm run build`, `npm run preview`, `npm run lint`, `npm run format`.

## Data source

The app ships with a complete mock fixture set so it runs standalone. One env var swaps every screen to the live backend — no component changes.

```bash
cp .env.example .env
```

| Variable              | Meaning                                                                                   |
| --------------------- | ----------------------------------------------------------------------------------------- |
| `VITE_DATA_SOURCE`    | `mock` (default) reads `src/mocks/*`; `api` calls the backend                             |
| `VITE_API_BASE_URL`   | Base URL for API calls. Leave empty to use the dev proxy                                  |
| `VITE_BACKEND_ORIGIN` | Backend the Vite dev proxy forwards `/api` and `/ws` to (default `http://127.0.0.1:8000`) |

In dev, Vite proxies `/api` and `/ws` to the FastAPI backend, so the browser sees one origin and CORS never applies.

## Run locally (full-stack)

This project runs a FastAPI backend and a Vite frontend. The frontend can run standalone (mock data) or connect to the backend (`VITE_DATA_SOURCE=api`). Follow these steps to run both locally.

1. Backend (Python)

```bash
# create a virtual environment and activate it
python -m venv .venv && source .venv/bin/activate

# install dependencies (including dev extras)
pip install -e ".[dev]"

# download spaCy model used by the repo
python -m spacy download en_core_web_sm

# copy example env and edit credentials (MISTRAL/ANTHROPIC, GITHUB_TOKEN, REDIS_URL)
cp .env.example .env

# start Redis (choose one):
brew services start redis            # macOS Homebrew
# OR, using Docker Compose from project root:
docker compose up -d

# start the API (from project root, with venv active)
.venv/bin/uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

2. Frontend (Node)

```bash
cd frontend
npm install
# copy frontend env and set VITE_DATA_SOURCE=api (or leave as mock)
cp .env.example .env
export VITE_DATA_SOURCE=api
npm run dev
# open http://localhost:5173
```

Notes:

- If `uvicorn` reports permission or command-not-found, ensure the venv is activated or call the binary directly as shown above.
- For development you can keep `VITE_DATA_SOURCE=mock` to use local fixtures without running the backend.
- To start a pipeline run against the bundled demo repo, use:

```bash
curl -X POST http://127.0.0.1:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "vulnapi"}'
```

If you want, I can also add a short consolidated run section to the root `README.md` — say if you'd like a single place with the exact commands.

## Architecture

Two seams keep the UI independent of where data comes from:

- **`src/lib/runService.ts`** — the only module that decides mock vs. API. Every screen calls it; no component fetches directly. Endpoint paths live in `src/lib/api.ts`.
- **`src/components/proofix/mockEventStream.ts`** — owns _all_ execution timing. `useExecutionRun` is a pure reducer over `ExecutionEvent`s, so replacing the mock factory with a WebSocket or SSE source drives the live journal without touching the hook or any component.

### Layout

Three columns: a minimal left `Sidebar` (repos + recent runs), the center execution journal, and a right evidence/report panel.

The journal renders one expandable `AgentCard` per agent, each with a purpose, a progressively revealed execution narrative, metrics, and an `AgentVisualization` — a bespoke visual per agent kind (`repo-intel`, `deps`, `static`, `reproduce`, `root`, `blast`, `planner`, `patch`, `mutation`, `merge`), typed in `visualizationTypes.ts`. `RetrySequence` renders repair attempts as a vertical chain; `RunReport` fades in once the run settles.

### Key directories

```
src/
  routes/            file-based routes (__root.tsx, index.tsx)
  components/
    proofix/         app components + agent data model
    ui/              Radix/shadcn primitives
  lib/               api config, run service, error handling
  mocks/             fixture data behind the runService seam
  hooks/
```

## Backend contract

`src/lib/api.ts` declares the endpoints the UI expects. Aligning them with the FastAPI backend in `../backend` is what integration work involves.
