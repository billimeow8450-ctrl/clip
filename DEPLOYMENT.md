# Deployment Guide — Supabase (DB) + Resend (email) + Render (hosting)

> **About account access:** I can't log into Supabase, Resend, or Render for you
> (no browser/account access in this environment) — but everything below is
> copy-paste. Budget: **~10 minutes** across the three dashboards.

---

## Step 1 — Supabase: create the database (~3 min)

1. Go to [supabase.com](https://supabase.com) → **New project** (or open your existing one).
2. Choose a name + region, set the **database password** (save it — you'll need it in a second).
3. When the project is ready: **Connect** (top bar) → **Connection string → URI** tab →
   pick the **Connection pooler** (port `6543`) copy. It looks like:

   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

4. **The schema is created automatically** on the backend's first boot (`init_db()` runs
   idempotently). If you prefer to create it manually:
   **SQL Editor → New query** → paste all of [`supabase/schema.sql`](supabase/schema.sql) → **Run**.
   (Safe to run before or after first boot — every statement is `IF NOT EXISTS`.)

5. Copy the URI somewhere safe — it goes into Render in Step 3.

> Use the **pooler** URI (port `6543`). Serverless-ish hosts like Render are fine with it and
> it avoids IPv4/IPv6 add-on issues. Do **not** use the "Connection pooling" port `5432`
> transaction string in app configs that keep long-lived pools... when in doubt, port `6543`.

---

## Step 2 — Resend: enable password-reset emails (~4 min)

The backend already supports Resend natively (HTTP API — no SMTP ports to open).

1. Go to [resend.com](https://resend.com) → sign in → **API Keys → Create API Key**
   (full access). Copy the `re_...` key.
2. **Sandbox mode (works immediately):** with no other config, mail is sent from
   `onboarding@resend.dev`, which **only delivers to the email address that owns your
   Resend account**. Great for testing the flow yourself today.
3. **Production mode (when ready):** **Resend → Domains → Add domain** (e.g. `yourdomain.com`),
   add the DNS records Resend shows (SPF/DKIM) at your registrar, then set:

   ```
   EMAIL_FROM="Clip Studio <no-reply@yourdomain.com>"
   ```

4. **Free tier:** 3,000 emails/month, 100/day — plenty for password resets.

> **Do NOT set `RESET_TOKEN_DEBUG_ECHO=1` anywhere in production** — it prints reset tokens
> to server logs. It exists for local dev only.

---

## Step 3 — Render: host backend + frontend (~3 min)

### 3a. Backend (FastAPI web service)

1. [render.com](https://render.com) → **New → Web Service** → connect this Git repo.
   Render reads `render.yaml` and pre-fills everything below.
2. Runtime **Python**, Build `pip install -r backend/requirements.txt`,
   Start `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 2`,
   Health check path `/api/health`.
3. **Environment variables** (Render reads `render.yaml`; these are the ones you must paste):

   | Key | Value | Source |
   |---|---|---|
   | `DATABASE_URL` | the Supabase pooler URI from Step 1 | Supabase → Connect |
   | `RESEND_API_KEY` | `re_...` API key | Resend → API Keys |
   | `EMAIL_FROM` | `Clip Studio <no-reply@yourdomain.com>` (or leave unset for sandbox) | Resend → Domains |

   Everything else is pre-generated or pre-filled (`JWT_SECRET_KEY`, `FILE_URL_SECRET`,
   `ENVIRONMENT=production`, `TRUSTED_PROXY_COUNT=1`, quotas, etc.).
4. Deploy, then confirm `https://<your-backend>.onrender.com/api/health` returns
   `{"status": "healthy"}`.

### 3b. Frontend (static site)

1. **New → Static Site** → same repo. Render reads `render.yaml`: build
   `cd frontend && npm install && npm run build`, publish `frontend/dist`,
   rewrite `/* → /index.html`.
2. `VITE_API_BASE_URL` is pulled from the backend service automatically
   (`fromService` in `render.yaml`). If creating manually, set it to your backend URL.
3. Deploy, then open the site and register a real account — the user lands in Supabase.

---

## Step 4 — Verify (5 min, do not skip)

1. **DB:** Supabase → Table Editor → `users`, `jobs`, `clips`, `files`, `password_resets`,
   `revoked_tokens` all exist. Register a user in the app → row appears.
2. **Email (sandbox):** click **Forgot password** for your own account → email arrives
   from `onboarding@resend.dev` → click the link → the reset form opens **with the token
   prefilled** → set a new password → sign in.
3. **Full flow:** register → login → upload → run a clipper job → download via the
   signed URL → log out → confirm the old token no longer works (revoked).
4. **Prod email:** once your Resend domain is verified + `EMAIL_FROM` set, repeats of
   step 2 should come from your domain and reach any address.

---

## Local development

```bash
cp backend/.env.example backend/.env   # then edit: DATABASE_URL can stay empty (SQLite)
./run_studio.sh                        # backend :8000 + frontend :5173
```

Local defaults are dev-friendly: SQLite fallback, `RESET_TOKEN_DEBUG_ECHO=1`
(reset tokens appear in the backend log instead of email).

---

## Enabling real AI processing later

`ENABLE_HEAVY_RENDERING=0` runs the honest simulation mode (clearly labeled in the UI).
Real FFmpeg/Whisper rendering needs the headless deps in the `Dockerfile` and real CPU.
When ready: set `ENABLE_HEAVY_RENDERING=1` on the backend service and size the instance
accordingly (keep `MAX_CONCURRENT_JOBS` modest on small instances).

---

## Go-live checklist

- [ ] `DATABASE_URL` (Supabase pooler URI) set on Render, `/api/health` green
- [ ] `RESEND_API_KEY` set; reset email received in a real inbox
- [ ] `EMAIL_FROM` set from a **verified Resend domain** before inviting real users
      (sandbox sender only reaches your own address)
- [ ] `RESET_TOKEN_DEBUG_ECHO` unset/`0` in production
- [ ] Register → login → upload → job → signed download works end-to-end
- [ ] Forgot-password round-trip works from a fresh browser (deep-link prefills the token)
- [ ] CI green on the deployed commit
