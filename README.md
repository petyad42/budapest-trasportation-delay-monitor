# Budapest Public Transport Delay Monitor

Monitors delays on Budapest public transport using **public GTFS** +
**GTFS-Realtime** data and **weather** (Open-Meteo). It shows live delays, ranks
problematic routes, and predicts expected travel time with a scikit-learn model.
No paid APIs.

The app has two parts that run together:

- **Backend** — a FastAPI (Python) service that serves the `/api/*` endpoints.
- **Frontend** — a React + TypeScript dashboard (Vite, Tailwind, Leaflet) that
  is the actual website you open in the browser.

---

## Quick start (TL;DR)

Open **two terminals**. Run the backend in one, the frontend in the other.

**Terminal 1 — backend (http://localhost:8000):**
```bash
cd budapest-transit-monitor-2
source .venv/bin/activate
uvicorn backend.main:app --reload
```

**Terminal 2 — frontend (http://localhost:5173):**
```bash
cd budapest-transit-monitor-2/frontend
npm run dev
```

Then open **http://localhost:5173** in your browser. That's the website.

> The frontend proxies `/api` requests to the backend on port 8000, so **both
> have to be running** at the same time.

To stop either one, press `Ctrl+C` in its terminal.

---

## First-time setup

Only needed once (or after dependencies change). After this, use the Quick start
above.

### 1. Backend

```bash
cd budapest-transit-monitor-2

# Create and activate a virtual environment (skip if .venv already exists)
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Create your env file (optional — see "Configuration" below)
cp .env.example .env
```

### 2. Frontend

```bash
cd budapest-transit-monitor-2/frontend

# Install Node dependencies (skip if node_modules already exists)
npm install

# Optional: create your frontend env file
cp .env.example .env
```

Requires Node.js (v18+) and npm installed on your machine.

---

## Daily workflow

Once setup is done, starting the site is just the two commands from the Quick
start. A typical session:

1. Terminal 1: `cd budapest-transit-monitor-2 && source .venv/bin/activate && uvicorn backend.main:app --reload`
2. Terminal 2: `cd budapest-transit-monitor-2/frontend && npm run dev`
3. Browser: open http://localhost:5173
4. When done, `Ctrl+C` in both terminals.

---

## Configuration

### Backend (`.env` in `budapest-transit-monitor-2/`)
Copy from `.env.example`. Everything has sane defaults, so the app runs out of
the box.

- `BKK_API_KEY` — a **free** BKK OpenData key (register at
  https://opendata.bkk.hu/). If left empty, the app runs in **mock mode** so
  nothing breaks.
- `API_HOST` / `API_PORT` — backend bind address (default `0.0.0.0:8000`).
- `POLL_INTERVAL_SECONDS`, `STALENESS_THRESHOLD_SECONDS`,
  `HTTP_TIMEOUT_SECONDS` — polling and freshness tuning.
- `CITY_LAT` / `CITY_LON` — default Budapest coordinates.

> **Security note:** `.env.example` currently has a real-looking `BKK_API_KEY`
> committed to it. Secrets should not live in a tracked example file. Consider
> blanking it in `.env.example` and keeping the real key only in your local
> `.env` (which should be git-ignored).

### Frontend (`.env` in `budapest-transit-monitor-2/frontend/`)
Copy from `frontend/.env.example`.

- `VITE_API_BASE_URL` — base URL the API client calls (default `/api`).
- `VITE_BACKEND_ORIGIN` — origin the dev-server `/api` proxy forwards to
  (default `http://localhost:8000`).

---

## Useful commands

### Backend
```bash
cd budapest-transit-monitor-2
source .venv/bin/activate

uvicorn backend.main:app --reload   # start the API with auto-reload
pytest                              # run the backend test suite
```

### Frontend
```bash
cd budapest-transit-monitor-2/frontend

npm run dev       # start the Vite dev server (the website)
npm run build     # type-check + production build into dist/
npm run preview   # preview the production build
npm run lint      # run ESLint
npm run test      # run the Vitest suite once
```

---

## Key API endpoints

The backend exposes a `/api/*` surface (visit http://localhost:8000/docs for the
interactive FastAPI docs). Health check: http://localhost:8000/health.

---

## Troubleshooting

- **Website loads but shows no data / API errors:** make sure the backend
  terminal is also running on port 8000.
- **`uvicorn: command not found`:** the virtual environment isn't activated —
  run `source .venv/bin/activate` first.
- **`npm run dev` fails about missing packages:** run `npm install` in the
  `frontend/` folder.
- **Port already in use:** another process is on 8000 or 5173. Stop it, or
  change `API_PORT` (backend) / the `port` in `frontend/vite.config.ts`.
