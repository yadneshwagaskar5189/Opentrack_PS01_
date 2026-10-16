# Institute Copilot: frontend

React 19 + Vite + TypeScript + Tailwind, with the warm "governance" design. Every screen talks to the FastAPI backend; there is no mock data.

## Run it

Needs Node.js 18+ and the backend running on port 8000.

```
npm install
npm run dev
```

Open http://localhost:5173 and pick a demo account. Requests to `/api` are forwarded to the backend by the Vite dev server.
Backend on another laptop? Create `.env` here with `VITE_BACKEND=http://192.168.x.x:8000` and restart `npm run dev`.

Projector tip: the design uses a compact type scale. Set the browser zoom to 125% (Ctrl +) for demos.

## Pages

| Page | Backend calls |
|---|---|
| Sign in (4 demo accounts from `seed_data/users.csv`, password `demo123`) | `POST /login`, `GET /me` |
| Dashboard | `GET /dashboard`, owner can `POST /admin/reset-demo` |
| Copilot Studio | `POST /copilot/ask`, "Try to break it" uses `POST /security/try-sql` |
| Approvals | `GET /actions`, `POST /actions/{id}/approve`, `POST /actions/{id}/reject` |
| Timetable | `GET /timetable`, `POST /timetable/fix-clashes`, `/trainer-unavailable`, `/add-batch`, `GET /timetable/requests`, `/courses` |
| Students at risk | `GET /students/at-risk` |
| Students | `GET /students` |
| Fee reconciliation (owner, accounts) | `GET /fees/reconciliation` |
| Audit log | `GET /audit` |

The "View as" switcher in the header signs in as another demo role, so judges can compare permissions live.

## Files

- `src/services/api.ts`: every backend call and the demo accounts (change `DEMO_ACCOUNTS` if your `users.csv` changes)
- `src/types/index.ts`: response shapes
- `src/components/AppShell.tsx`: sidebar, header, role switcher, which pages each role sees
- `src/components/ActionPreview.tsx`: approval cards (editable WhatsApp drafts, mark paid, timetable changes)
- `src/views/*View.tsx`: one file per page
- `src/index.css`: the design; live-backend additions are at the end

`npm run build` makes a production build in `dist/`. `npm run typecheck` runs TypeScript.
Libraries: React, Vite, TypeScript, Tailwind CSS, lucide-react, framer-motion (all MIT).
