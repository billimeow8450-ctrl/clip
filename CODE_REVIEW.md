# Pre-Launch Code Review — OpusPulse / ClipStudio AI

**Reviewer:** Buffy (staff-engineer review) · **Date:** Sept 21, 2026
**Scope:** Full repository — FastAPI backend, React/Vite frontend, MasterEngine source, deploy configs.
**Verification at review time:** 19 backend tests passed. No code was modified during the review.

---

## ✅ Remediation status (updated Sept 21, 2026)

All Critical and nearly all Medium/Low findings have since been fixed in this working tree.
**26/26 backend tests pass; frontend builds cleanly (`vite build`, 0 lint errors).**
Deployment instructions: `DEPLOYMENT.md`.

| Finding | Status |
|---|---|
| C1 reset token in API response (account takeover) | **Fixed** — delivered by SMTP only (`backend/utils/emailer.py`); response returns generic message |
| C2 fabricated results in default deploy | **Fixed** — simulation results clearly labeled in UI/API; fake download paths removed |
| C3 unauthenticated file downloads (IDOR) | **Fixed** — HMAC-signed expiring URLs bound to owner + Bearer/DB ownership check (`backend/routers/files.py`) |
| C4 blocking SQLite I/O in event loop | **Fixed** — all SQLite calls run via `asyncio.to_thread` (`backend/database.py`) |
| C5 no upload quotas / content checks | **Fixed** — per-user byte+count quotas, magic-byte validation, upload rate limit, `DELETE /api/files/{id}` |
| C6 no token revocation | **Fixed** — `jti` revocation table, `POST /api/auth/logout`, `token_version` bump on password reset |
| M1–M2 DB layer fragility / missing indexes | **Fixed** — quote-aware PG placeholder conversion, idempotent migrations, 8 new indexes |
| M3 spoofable `X-Forwarded-For` rate limiting | **Fixed** — right-most untrusted hop via `TRUSTED_PROXY_COUNT` (`backend/utils/rate_limit.py`) |
| M4 fire-and-forget tasks | **Fixed** — semaphore-bounded `spawn_job`, startup job reaper, 24h output retention cleanup |
| M6 SSRF hardening | **Partially fixed** — checks retained; DNS-rebinding TOCTOU remains a documented residual risk |
| M7 missing headers & logout | **Fixed** — security-headers middleware (nosniff, DENY, HSTS in prod, referrer policy) + logout endpoint |
| M8 unbacked marketing claims | **Fixed** — fake metrics/testimonials/status indicator removed; factual copy |
| M9 test & CI gaps | **Partially fixed** — 7 new security tests + GitHub Actions CI (`.github/workflows/ci.yml`); job-endpoint coverage still thin |
| M10 dead theme files / prod API fallback | **Fixed** — unused theme files deleted; localhost fallback only in dev mode |
| L1–L10 low-priority items | **Mostly fixed** — password policy, username validation, pagination, `useJobPolling` hook, mobile nav, FAQ a11y, transcript filter crash, unused deps removed (`sqlalchemy`) |
| C2 root cause (real AI rendering) | **Open** — needs a render-capable host with `ENABLE_HEAVY_RENDERING=1`; honest simulation ships until then |
| M5 MasterEngine monolith | **Open** — vendored tree unchanged; extract to versioned library as follow-up |

Note: Render/Supabase dashboard access is outside this environment — see `DEPLOYMENT.md`
for the exact env vars to paste (secrets go dashboard-to-dashboard, never through chat).

---

## Executive Summary

The project is a two-service SaaS scaffold: a FastAPI backend with SQLite/PostgreSQL dual-database
support, JWT auth, file uploads, and three "AI job" pipelines (clipper, editor, transcriber), plus a
React 19 + Vite + Tailwind 4 frontend. Security fundamentals were better than typical for this
stage — bcrypt hashing, rate limiting, SSRF validation, path-traversal protection, and a production
guard against default secrets all existed and were tested. However the product was not launchable:
with `ENABLE_HEAVY_RENDERING=0` every "AI clip", viral score, and transcript was a hardcoded
fixture marketed as real AI output; the password-reset flow handed the reset token back in the HTTP
response (full account takeover for any known email); every generated media file was served publicly
with no ownership check; background jobs were unbounded fire-and-forget tasks; and the SQLite data
layer ran synchronous blocking I/O inside async handlers. Test coverage covered none of the
job endpoints and there was no CI. Landing-page claims ("850,000+ creators", "4.2B+ views",
"Max 2 GB uploads") were unbacked and contradicted the implemented 500 MB limit.

---

## Critical issues (as found)

- **C1.** `backend/routers/auth.py` returned `reset_token` in the forgot-password response for any
  registered email; no email transport existed. Complete account-takeover chain.
- **C2.** `render.yaml`/`Dockerfile` set `ENABLE_HEAVY_RENDERING=0`; `backend/worker.py` returned
  three hardcoded clips with fixed scores and fake output paths — 404 downloads, fake
  "Predictive Virality Score™".
- **C3.** `GET /api/files/{filename}` served uploads/outputs to unauthenticated callers; the
  `files` ownership table was never consulted.
- **C4.** Synchronous `sqlite3` behind an async-shaped wrapper blocked the event loop for up to
  30s under write contention, freezing all concurrent requests.
- **C5.** Uploads had no per-user quota, no content validation (extension-only), and no cleanup;
  auto-created guest accounts with a hardcoded password (`LandingPage.jsx`) amplified abuse.
- **C6.** No JWT revocation; "logout" was client-side only; 7-day tokens lived forever.

## Medium-priority issues (as found)

M1 fragile SQLite→PG SQL rewriting (naive `?`→`$n`, auto-RETURNING, no-op commit);
M2 schema drift and missing indexes on jobs/clips/files;
M3 left-most XFF trusted unconditionally (rate-limit bypass) and per-process limiter;
M4 `asyncio.create_task` without references/GC hazard, no restart recovery, no concurrency cap;
M5 `WEBSITE_DEVELOPER_SOURCE` path-hacked into the web app, bot importing backend routers;
M6 SSRF: DNS-rebinding TOCTOU; blocking `getaddrinfo` in the request path;
M7 no security headers, no logout/refresh story, wildcard CORS methods;
M8 fabricated social proof and dead footer links;
M9 19 tests covered no job/clip/project endpoints; no CI; no frontend tests;
M10 two dead theme systems, `http://localhost:8000` prod API fallback, unused deps
(`sqlalchemy[asyncio]`, `aiosqlite`).

## Low-priority issues (as found)

L1 weak Pydantic validation (free-text durations, unbounded floats, no username charset, 6-char
passwords); L2 `get_optional_user` swallowed all exceptions; L3 unused deps and unpinned ranges;
L4 polling re-created `setInterval` on every state update; L5 1,336-line LandingPage with
copy-pasted blocks; L6 four product names in use; L7 `.isdigit()` parse quirk on `target_duration`;
L8 hardcoded `LIMIT 50` with no pagination; L9 PG `lastrowid` emulation unchecked; L10
`run_studio.sh` binds `0.0.0.0` with `--reload`.

## UI/UX findings (ranked, as found)

U1 broken download buttons after "successful" jobs; U2 uncompletable password-reset UI (no email
delivery); U3 contradictory limits (2 GB vs 500 MB, "10 clips" vs 3, unmetered "75 free minutes");
U4 teal (utility pages) vs electric-blue (landing) design schism; U5 accessibility gaps (no
`aria-expanded` on FAQ, no dialog semantics/focus trap/Escape on modals, unlabeled hero input,
mouse-only timeline handles); U6 hardcoded filter counts; U7 duplicate `id="features"`; U8 fake
"systems operational" indicator; U9 no mobile navigation; U10 landing errors rendered off-screen.

## Go / No-Go (original)

**NO-GO** for public launch as reviewed. The combined effect of fabricated results, account
takeover via reset, and public user-media access was individually launch-blocking. With the
remediations above applied, the blocking items that remain are operational: provision SMTP,
set Render/Supabase env vars (`DEPLOYMENT.md`), and decide when to enable real rendering
(`ENABLE_HEAVY_RENDERING=1`) — or launch as an explicitly-labeled simulation beta first.
