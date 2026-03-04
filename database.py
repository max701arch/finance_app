import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_DB_FILE = BASE_DIR / "finance.db"

_db_path = (os.getenv("FINANCE_DB_PATH") or "").strip()
if _db_path:
    DB_FILE = Path(_db_path).expanduser()
    if not DB_FILE.is_absolute():
        DB_FILE = (BASE_DIR / DB_FILE).resolve()
else:
    DB_FILE = _DEFAULT_DB_FILE


def get_connection():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _has_column(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                date TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        if not _has_column(cursor, "transactions", "user_id"):
            cursor.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_type_date ON transactions(user_id, type, date)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_category ON transactions(user_id, category)"
        )

        conn.commit()