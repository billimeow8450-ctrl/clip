-- ClipStudio AI — Supabase / PostgreSQL schema
--
-- OPTIONAL: the backend creates all of this automatically on first boot
-- (backend/database.py -> init_db()). Use this file only if you prefer to set
-- up the tables manually from the Supabase dashboard:
--   Supabase Dashboard -> SQL Editor -> New query -> paste -> Run
--
-- Paste your DATABASE_URL (Connection pooler, port 6543) into Render; nothing
-- else is needed. Every statement here is idempotent (IF NOT EXISTS), so it is
-- safe to run before OR after the backend's first boot.

-- ---------------------------------------------------------------- users -----
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    username        VARCHAR(255) UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    tier            VARCHAR(50) DEFAULT 'pro',
    token_version   INTEGER NOT NULL DEFAULT 0, -- bumped on password reset; invalidates all JWTs
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------- projects -----
CREATE TABLE IF NOT EXISTS projects (
    id            VARCHAR(100) PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    source_type   VARCHAR(50) NOT NULL,
    source_url    TEXT,
    duration      REAL DEFAULT 0,
    thumbnail_url TEXT,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- jobs ------
CREATE TABLE IF NOT EXISTS jobs (
    id            VARCHAR(100) PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id    VARCHAR(100),
    type          VARCHAR(50) NOT NULL,
    status        VARCHAR(50) NOT NULL DEFAULT 'queued',
    progress      REAL NOT NULL DEFAULT 0.0,
    stage         VARCHAR(100) NOT NULL DEFAULT 'Queued',
    input_params  TEXT,
    result_data   TEXT,
    error_message TEXT,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- --------------------------------------------------------------- clips ------
CREATE TABLE IF NOT EXISTS clips (
    id            VARCHAR(100) PRIMARY KEY,
    job_id        VARCHAR(100) NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    start_time    REAL NOT NULL,
    end_time      REAL NOT NULL,
    duration      REAL NOT NULL,
    viral_score   REAL DEFAULT 0,
    hook_text     TEXT,
    video_url     TEXT,
    thumbnail_url TEXT,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- --------------------------------------------------------------- files ------
CREATE TABLE IF NOT EXISTS files (
    id            VARCHAR(100) PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------- password_resets ------
CREATE TABLE IF NOT EXISTS password_resets (
    id         VARCHAR(100) PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token      VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used       INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------- revoked_tokens -------
-- JWT revocation (logout) + token_version-based session invalidation.
CREATE TABLE IF NOT EXISTS revoked_tokens (
    id         VARCHAR(100) PRIMARY KEY,
    jti        VARCHAR(64) UNIQUE NOT NULL,
    user_id    INTEGER,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------- indexes ------
CREATE INDEX IF NOT EXISTS idx_jobs_user_id            ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status             ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_clips_user_id           ON clips(user_id);
CREATE INDEX IF NOT EXISTS idx_clips_job_id            ON clips(job_id);
CREATE INDEX IF NOT EXISTS idx_files_user_id           ON files(user_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_user_id ON password_resets(user_id);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_jti      ON revoked_tokens(jti);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires  ON revoked_tokens(expires_at);

-- ------------------------------------------------------- housekeeping -------
-- Optional but recommended on Supabase: the backend relies on the app to clean
-- expired reset tokens / revoked tokens; a cron keeps the DB tidy regardless.
-- Supabase Dashboard -> Database -> Cron (pg_cron) -> add:
--   SELECT cron.schedule('purge-expired-auth-rows', '0 3 * * *', $$
--     DELETE FROM password_resets WHERE expires_at < now() - interval '1 day';
--     DELETE FROM revoked_tokens  WHERE expires_at < now() - interval '1 day';
--   $$);
--
-- NOTE ON ROW LEVEL SECURITY (RLS): Supabase enables RLS by default only for
-- tables exposed through its auto-generated REST API. This app connects with
-- the direct Postgres connection string (service role, server-side only) and
-- never exposes the database to the browser, so RLS is neither required nor
-- used here. Do NOT publish this project's API keys client-side.
