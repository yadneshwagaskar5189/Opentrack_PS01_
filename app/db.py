"""Database connections.

- get_conn():              normal read-write connection for the app's own code.
- get_readonly_conn(user): connection used ONLY for copilot-generated SQL.
                           Opened read-only, query_only on, and scoped to the user
                           (current_trainer_id() returns the trainer's id).
"""
import sqlite3
from pathlib import Path

from . import config

APP_DIR = Path(__file__).resolve().parent


def _register_functions(conn: sqlite3.Connection, trainer_id: int | None) -> None:
    # Used by the t_my_* views to filter rows to the logged-in trainer.
    # Returns NULL for non-trainers, so t_my_* views return nothing for them.
    conn.create_function("current_trainer_id", 0, lambda: trainer_id, deterministic=True)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _register_functions(conn, None)
    return conn


def get_readonly_conn(trainer_id: int | None = None) -> sqlite3.Connection:
    uri = f"file:{Path(config.DB_PATH).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    _register_functions(conn, trainer_id)
    return conn


def init_db(reset: bool = False) -> None:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        if reset:
            # Drop in place (deleting an open file fails on Windows)
            conn.execute("PRAGMA foreign_keys = OFF")
            objs = conn.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
                                "AND type IN ('view','table')").fetchall()
            for kind, name in sorted(objs, key=lambda o: o[0] != "view"):
                conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
            conn.commit()
        conn.executescript((APP_DIR / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((APP_DIR / "views.sql").read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
