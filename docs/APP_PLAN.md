# Reno PM — installable app (PWA) rebuild

Replaces the Streamlit front end. Supabase, the Telegram bots (Jeeves/Matty),
the nightly GitHub Action and the `services/` logic stay exactly as they are.

## Why
Streamlit re-runs the whole script on every tap and reloads each screen; its
widgets don't scale for touch editing. A real client-side app fixes both.

## Architecture
- **Backend** `api/` — FastAPI, reuses `services/`. All data access goes through
  it; the browser never sees the Supabase service key. Enforces roles server-side.
- **Frontend** `web/` — React + TypeScript + Vite, installable PWA (manifest +
  service worker), mobile-first. Built into static files served by the API.
- **One container** `renopm-app` (multi-stage Docker: node builds, python serves)
  on its own subdomain. Streamlit (`renopm`) keeps running until cutover.
- **Auth** — own accounts in `app_users` (scrypt password hashes, signed JWT in an
  httpOnly cookie). No dependency on Supabase Auth config.

## Roles
| Role | Who | Can do |
|---|---|---|
| owner | Duncan | everything: budgets, draws, materials, SOW upload, delete/archive, approvals, users |
| pm | Tiffany | see everything; edit schedule + task status/progress, journal links; budget screens read-only |
| contractor | Hugo etc. | see schedule for assigned properties; propose status/progress/date changes → `change_requests`, applied only when the owner approves |

Contractors are tied to properties via `user_properties`.

## New tables (scripts/migration_app_auth.sql)
`app_users`, `user_properties`, `change_requests`.

## Screens (all built before cutover)
Login · Dashboard · Schedule (Gantt + tap-to-edit list, AI timeline adjust,
publish to Telegram, nudges) · Budget (KPIs, materials by unit, draws,
receipts import, email sync, unit assignment, labor+materials flags) ·
Journal (grouped photos, task links) · Material Logs · Upload SOW (xlsx/csv/pdf) ·
Approvals · Users · public read-only client schedule link (keeps existing
Telegram portal links working).

## Cutover
New app on its own subdomain → verify → repoint `BASE_URL` (Telegram links) →
retire Streamlit container.
