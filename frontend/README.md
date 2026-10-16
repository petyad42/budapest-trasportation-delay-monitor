# Budapest Transit Monitor — Frontend

React + TypeScript dashboard for the Budapest Public Transport Delay Monitor,
built with Vite, Tailwind CSS v4, Leaflet (via react-leaflet), and TanStack
React Query.

## Scripts

- `npm run dev` — start the Vite dev server (default http://localhost:5173).
  Requests to `/api` are proxied to the backend at `http://localhost:8000`.
- `npm run build` — type-check and produce a production build in `dist/`.
- `npm run lint` — run ESLint.
- `npm run test` — run the Vitest suite once (React Testing Library + jsdom).

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

- `VITE_API_BASE_URL` — base URL the API client calls (default `/api`).
- `VITE_BACKEND_ORIGIN` — origin the dev-server `/api` proxy forwards to.

## Structure

- `src/lib/api.ts` — API client that unwraps the shared `data_status` envelope
  into `{ data, status }`.
- `src/lib/types.ts` — TypeScript shapes mirroring the backend Pydantic models.
- `src/lib/colors.ts` — shared mode-to-color module mirroring `backend/colors.py`.
- `src/lib/queryClient.ts` — configured React Query client (polling-friendly).
- `src/App.tsx` — dashboard shell; widgets/panels are filled in by later tasks.
