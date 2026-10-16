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
    # FastAPI may open the connection (in a dependency) on one worker thread and use it on another.
    # Each request still gets its own connection, so turning off the same-thread check is safe.
    conn = sqlite3.connect(config.DB_PATH, timeout=10, check_same_thread=False)
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


def upgrade() -> None:
    """Bring an existing database up to date in place (nothing is deleted)."""
    import json
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(students)")}
        if cols and "real_contact" not in cols:
            conn.execute("ALTER TABLE students ADD COLUMN real_contact INTEGER NOT NULL DEFAULT 0")
            # students already added through the app had their real number typed in by staff
            for payload, result in conn.execute("SELECT payload_json, result_json FROM pending_actions "
                                                "WHERE action_type='add_student' AND status='approved'").fetchall():
                try:
                    if json.loads(payload).get("existing_student_id") is None:
                        sid = json.loads(result or "{}").get("student_id")
                        if sid:
                            conn.execute("UPDATE students SET real_contact=1 WHERE id=?", (sid,))
                except ValueError:
                    pass
            conn.commit()
        pay_cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)")}
        if pay_cols and "receipt_no" not in pay_cols:
            conn.execute("ALTER TABLE payments ADD COLUMN receipt_no TEXT")
            conn.commit()
        audit_cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}
        if audit_cols and "hash" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN prev_hash TEXT")
            conn.execute("ALTER TABLE audit_log ADD COLUMN hash TEXT")
            conn.commit()
        if audit_cols:
            from . import audit
            audit.seal(conn)                                  # seal entries written before sealing existed
            conn.commit()
        if cols:
            # views hold no data: rebuild them so new or changed views (e.g. v_schedule) appear
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
                conn.execute(f'DROP VIEW IF EXISTS "{name}"')
            conn.commit()
            conn.executescript((APP_DIR / "views.sql").read_text(encoding="utf-8"))
            conn.commit()
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:        # database from an older version
        return default
    return row[0] if row and row[0] else default


def institute_name(conn: sqlite3.Connection) -> str:
    return get_setting(conn, "institute_name", "the institute")


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
