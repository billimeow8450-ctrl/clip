from __future__ import annotations

import pytest
from backend.database import _convert_sqlite_to_pg_query, is_postgres


def test_sqlite_to_pg_query_conversion():
    sql = "SELECT id, email FROM users WHERE email = ? OR username = ?;"
    pg_sql, is_insert = _convert_sqlite_to_pg_query(sql)
    assert pg_sql == "SELECT id, email FROM users WHERE email = $1 OR username = $2;"
    assert is_insert is False


def test_insert_query_adds_returning_id():
    sql = "INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)"
    pg_sql, is_insert = _convert_sqlite_to_pg_query(sql)
    assert "$1, $2, $3" in pg_sql
    assert "RETURNING id;" in pg_sql
    assert is_insert is True


def test_is_postgres_detection(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres.test:secret@aws-0.pooler.supabase.com:6543/postgres")
    assert is_postgres() is True

    monkeypatch.setenv("DATABASE_URL", "")
    assert is_postgres() is False
