import os
import re
import sqlite3
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
    """Convert SQLite ? placeholders to PostgreSQL $1, $2... and handle RETURNING id."""
    count = 0
    def replacer(match):
        nonlocal count
        count += 1
        return f"${count}"

    pg_query = re.sub(r"\?", replacer, query)
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
        try:
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
        except Exception as exc:
            raise exc

    async def commit(self) -> None:
        pass


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


class _DBContext:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def __await__(self):
        async def _open():
            return self
        return _open().__await__()

    async def __aenter__(self) -> "_SQLiteConnectionWrapper":
        # aiosqlite currently deadlocks on Python 3.14 in some environments.
        # SQLite operations here are deliberately small, so a synchronous
        # connection behind the existing async-shaped interface is reliable
        # and keeps callers identical across SQLite and PostgreSQL.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA busy_timeout=30000;")
        return _SQLiteConnectionWrapper(self._conn)

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn:
            if exc_type is not None:
                self._conn.rollback()
            self._conn.close()


class _SQLiteCursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor
        self.lastrowid = cursor.lastrowid

    async def fetchone(self) -> Optional[sqlite3.Row]:
        return self._cursor.fetchone()

    async def fetchall(self) -> List[sqlite3.Row]:
        return self._cursor.fetchall()


class _SQLiteConnectionWrapper:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, query: str, params: tuple = ()) -> _SQLiteCursor:
        return _SQLiteCursor(self._conn.execute(query, params))

    async def commit(self) -> None:
        self._conn.commit()


def get_db():
    if is_postgres():
        return _PGDBContext(os.getenv("DATABASE_URL", "").strip())
    return _DBContext(DB_PATH)


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

        await db.commit()
