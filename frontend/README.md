# BEREV ERP - frontend

React + TypeScript + Vite + Tailwind + React Router + TanStack Query + React Hook Form + Zod + Recharts + lucide-react.
Forest-green / ivory / gold theme. The backend and `db/` are not touched by this folder.

## Run
```powershell
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxies /api to http://127.0.0.1:8000
npm test           # vitest
npm run build      # typecheck + production build into dist/
```
Start the API separately (see `../backend/README.md`) and make sure PostgreSQL, migrations and the seed script have run
and the first admin exists, otherwise sign-in cannot work. Set `VITE_BACKEND_URL` (see `.env.example`) if the API is elsewhere.

## What works today
- Real sign-in/out against `/auth/login`, `/auth/refresh`, `/auth/me`, `/auth/logout`; silent token refresh on 401.
  Access token is kept in memory; the refresh token in `sessionStorage` (cleared when the tab closes).
- Role-aware menu from the server's permission list (the server still enforces everything).
- App shell: collapsible sidebar (drawer on mobile), breadcrumbs, Ctrl+K module search, notification menu, user menu, confirm dialogs, toasts, error boundary.
- Dashboard, list pages for every module, Settings and Profile. Loading, empty, error and "not available yet" states.

## Not done (needs backend endpoints first)
Only sign-in is a form today. Create/edit forms (customers, products, quotations, GST invoice entry, payments, expenses),
invoice PDF download/print, report exports and password change/reset need their API contracts first. The invoice form must
send lines to the server and display the server's computed totals (the backend `berev_core.gst` engine owns all tax maths).

## Verification status
`npm ci`, `npm test` (16 tests), and `npm run build` have been run successfully. The frontend dependency lockfile is checked in; use `npm ci` for reproducible installs. `npm audit` currently reports no known dependency vulnerabilities. Charts are split into a separate production chunk.

The UI still depends on the backend workflows listed above; a successful build does not mean those unfinished product features are available. shadcn/ui was not used; the small components in `src/components/ui.tsx` stand in.

See `docs/API_CONTRACT.md` for the endpoint shapes the screens expect.
