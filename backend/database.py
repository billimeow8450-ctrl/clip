import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_DIR = Path(__file__).resolve().parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "clip_studio.db"

_PG_POOL = None


def is_postgres() -> bool:
    url = os.getenv("DATABASE_URL", "").strip()
    return bool(url and ("postgres://" in url or "postgresql://" in url))


def _convert_sqlite_to_pg_query(query: str) -> tuple[str, bool]:
    """Convert SQLite ``?`` placeholders to PostgreSQL ``$1, $2, ...`` and handle RETURNING id.

    Placeholder replacement is quote-aware: a ``?`` inside a SQL string literal is
    preserved verbatim instead of being rewritten into a parameter reference.
    """
    count = 0
    out: List[str] = []
    in_string = False
    for ch in query:
        if ch == "'":
            # SQL escapes a literal quote by doubling it (''); toggling twice
            # returns us to the same in-string state, which is correct.
            in_string = not in_string
            out.append(ch)
            continue
        if ch == "?" and not in_string:
            count += 1
            out.append(f"${count}")
            continue
        out.append(ch)

    pg_query = "".join(out)
    is_insert = pg_query.strip().upper().startswith("INSERT INTO")
    has_returning = "RETURNING" in pg_query.upper()

    if is_insert and not has_returning:
        pg_query = pg_query.rstrip("; ") + " RETURNING id;"

    return pg_query, is_insert


class _PGCursor:
    def __init__(self, rows: Optional[List[Any]] = None, lastrowid: Optional[int] = None):
        self._rows = rows or []
        self._idx = 0
        self.lastrowid = lastrowid

    async def fetchone(self) -> Optional[Dict[str, Any]]:
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return dict(row)
        return None

    async def fetchall(self) -> List[Dict[str, Any]]:
        remaining = self._rows[self._idx:]
        self._idx = len(self._rows)
        return [dict(r) for r in remaining]


class _PGConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, query: str, params: tuple = ()) -> _PGCursor:
        pg_query, is_insert = _convert_sqlite_to_pg_query(query)
        if is_insert:
            records = await self._conn.fetch(pg_query, *params)
            last_id = records[0]["id"] if records and "id" in records[0] else None
            return _PGCursor(records, lastrowid=last_id)
        elif pg_query.strip().upper().startswith("SELECT"):
            records = await self._conn.fetch(pg_query, *params)
            return _PGCursor(records)
        else:
            await self._conn.execute(pg_query, *params)
            return _PGCursor()

    async def commit(self) -> None:
        # asyncpg runs each statement in its own implicit transaction; the
        # write is already durable when execute() returns.
        return None


class _PGDBContext:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn = None

    def __await__(self):
        async def _open():
            return self
        return _open().__await__()

    async def __aenter__(self) -> _PGConnectionWrapper:
        global _PG_POOL
        import asyncpg
        if _PG_POOL is None:
            # Normalize url for asyncpg if needed
            dsn = self.dsn
            if dsn.startswith("postgresql+asyncpg://"):
                dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
            _PG_POOL = await asyncpg.create_pool(
                dsn=dsn,
                min_size=1,
                max_size=10,
                statement_cache_size=0,
            )
        self._conn = await _PG_POOL.acquire()
        return _PGConnectionWrapper(self._conn)

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        global _PG_POOL
        if self._conn and _PG_POOL:
            await _PG_POOL.release(self._conn)


class _SQLiteCursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor
        self.lastrowid = cursor.lastrowid

    async def fetchone(self) -> Optional[sqlite3.Row]:
        return await asyncio.to_thread(self._cursor.fetchone)

    async def fetchall(self) -> List[sqlite3.Row]:
        return await asyncio.to_thread(self._cursor.fetchall)


class _SQLiteConnectionWrapper:
    def __init__(self, conn: sqlite3.Connection, lock: asyncio.Lock):
        self._conn = conn
        self._lock = lock

    async def execute(self, query: str, params: tuple = ()) -> _SQLiteCursor:
        # The lock serializes statement execution across worker threads for
        # this connection (a cursor must not interleave with another statement
        # on the same connection).
        async with self._lock:
            cursor = await asyncio.to_thread(self._conn.execute, query, params)
        return _SQLiteCursor(cursor)

    async def commit(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.commit)


class _DBContext:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def __await__(self):
        async def _open():
            return self
        return _open().__await__()

    async def __aenter__(self) -> _SQLiteConnectionWrapper:
        # SQLite operations run through asyncio.to_thread so disk I/O never
        # blocks the event loop (previously a 30s write lock froze every
        # concurrent HTTP request). The async-shaped interface is preserved so
        # callers remain identical across SQLite and PostgreSQL.
        #
        # check_same_thread=False is required because to_thread's worker pool
        # may dispatch successive calls to different threads; the per-context
        # threading.Lock below serializes access so this remains safe.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        def _connect() -> sqlite3.Connection:
            conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")
            return conn

        self._conn = await asyncio.to_thread(_connect)
        self._lock = asyncio.Lock()
        return _SQLiteConnectionWrapper(self._conn, self._lock)

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn:
            if exc_type is not None:
                await asyncio.to_thread(self._conn.rollback)
            await asyncio.to_thread(self._conn.close)
            self._conn = None


def get_db():
    if is_postgres():
        return _PGDBContext(os.getenv("DATABASE_URL", "").strip())
    return _DBContext(DB_PATH)


def _parse_db_timestamp(value: Any) -> Optional[datetime]:
    """Parse timestamps returned by either dialect (datetime or string)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _run_migrations(db) -> None:
    """Idempotent migrations that must not fail on fresh installs."""
    # users.token_version: bumped on password reset to invalidate all JWTs.
    try:
        await db.execute(
            "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
        )
    except Exception:
        pass  # Column already exists (SQLite raises, PG we use IF NOT EXISTS below)

    if is_postgres():
        await db.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0"
        )


async def _create_indexes(db) -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
        "CREATE INDEX IF NOT EXISTS idx_clips_user_id ON clips(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_clips_job_id ON clips(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_password_resets_user_id ON password_resets(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_revoked_tokens_jti ON revoked_tokens(jti)",
        "CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at)",
    ]
    for statement in indexes:
        await db.execute(statement)


async def init_db() -> None:
    if is_postgres():
        # Initialize PostgreSQL (Supabase) tables
        async with get_db() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    username VARCHAR(255) UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    tier VARCHAR(50) DEFAULT 'pro',
                    token_version INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id VARCHAR(100) PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    source_type VARCHAR(50) NOT NULL,
                    source_url TEXT,
                    duration REAL DEFAULT 0,
                    thumbnail_url TEXT,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id VARCHAR(100) PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    project_id VARCHAR(100),
                    type VARCHAR(50) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'queued',
                    progress REAL NOT NULL DEFAULT 0.0,
                    stage VARCHAR(100) NOT NULL DEFAULT 'Queued',
                    input_params TEXT,
                    result_data TEXT,
                    error_message TEXT,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS clips (
                    id VARCHAR(100) PRIMARY KEY,
                    job_id VARCHAR(100) NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    duration REAL NOT NULL,
                    viral_score REAL DEFAULT 0,
                    hook_text TEXT,
                    video_url TEXT,
                    thumbnail_url TEXT,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id VARCHAR(100) PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    original_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id VARCHAR(100) PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token VARCHAR(255) UNIQUE NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS revoked_tokens (
                    id VARCHAR(100) PRIMARY KEY,
                    jti VARCHAR(64) UNIQUE NOT NULL,
                    user_id INTEGER,
                    expires_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await _run_migrations(db)
            await _create_indexes(db)
        return

    # Fallback to local SQLite
    async with get_db() as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")

        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                tier TEXT DEFAULT 'pro',
                token_version INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Projects table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_url TEXT,
                duration REAL DEFAULT 0,
                thumbnail_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Jobs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                project_id TEXT,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0.0,
                stage TEXT NOT NULL DEFAULT 'Queued',
                input_params TEXT,
                result_data TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Clips table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                duration REAL NOT NULL,
                viral_score REAL DEFAULT 0,
                hook_text TEXT,
                video_url TEXT,
                thumbnail_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Uploaded files table for IDOR prevention & ownership
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Password resets table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Revoked JWTs (logout / token revocation support)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                id TEXT PRIMARY KEY,
                jti TEXT UNIQUE NOT NULL,
                user_id INTEGER,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await _run_migrations(db)
        await _create_indexes(db)

        await db.commit()
