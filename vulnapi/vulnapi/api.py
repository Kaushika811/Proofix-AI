"""Bug 2: SQL injection via string concatenation."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    conn.execute("INSERT OR IGNORE INTO users (id, name, email) VALUES (1, 'admin', 'admin@example.com')")
    conn.commit()
    conn.close()


def lookup_user(user_id: str) -> dict | None:
    """BUG: unsafe SQL string concatenation."""
    conn = get_connection()
    # Vulnerable: direct string interpolation
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor = conn.execute(query)
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def search_users(name_filter: str) -> list[dict]:
    conn = get_connection()
    query = f"SELECT * FROM users WHERE name LIKE '%{name_filter}%'"
    cursor = conn.execute(query)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
