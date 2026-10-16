"""Append-only audit log of everything the copilot and its users do.

Tamper evidence: every entry is sealed with an HMAC-SHA256 of its content plus the previous
entry's seal (a hash chain). The key lives outside the database (AUDIT_KEY in .env, or a random
key in .audit_key), so someone who edits or deletes a row in institute.db can't re-seal it.
verify() walks the chain and points at the first entry that no longer matches.
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime

from . import config

GENESIS = "genesis"
FIELDS = ("ts", "user_id", "username", "role", "event_type", "question", "sql_text", "outcome", "detail_json")
_KEY: bytes | None = None


def _key() -> bytes:
    global _KEY
    if config.AUDIT_KEY:
        return config.AUDIT_KEY.encode()
    if _KEY is None:
        path = config.ROOT / ".audit_key"
        try:
            _KEY = path.read_text(encoding="utf-8").strip().encode()
        except FileNotFoundError:
            _KEY = secrets.token_hex(32).encode()
            path.write_text(_KEY.decode(), encoding="utf-8")
    return _KEY


def _digest(values: tuple, prev: str) -> str:
    msg = json.dumps([prev, *values], ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()


def _has_seal_columns(conn) -> bool:
    return "hash" in {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}


def seal(conn) -> int:
    """Seal entries added after the last sealed one (normally just the newest). Returns how many."""
    if not _has_seal_columns(conn):
        return 0
    last = conn.execute("SELECT id, hash FROM audit_log WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    start, prev = (last[0], last[1]) if last else (0, GENESIS)
    rows = conn.execute(f"SELECT id, {', '.join(FIELDS)} FROM audit_log WHERE id > ? ORDER BY id", (start,)).fetchall()
    for r in rows:
        h = _digest(tuple(r[1:]), prev)
        conn.execute("UPDATE audit_log SET prev_hash=?, hash=? WHERE id=?", (prev, h, r[0]))
        prev = h
    return len(rows)


def verify(conn) -> dict:
    """Check every seal. Editing, deleting or re-ordering entries shows up as a broken link.
    (The chain alone cannot notice the newest entries being cut off; the result shows the last entry.)"""
    if not _has_seal_columns(conn):
        return {"ok": False, "entries": 0, "problems": [], "message": "This database has no audit seals yet."}
    prev, problems, n = GENESIS, [], 0
    for r in conn.execute(f"SELECT id, prev_hash, hash, {', '.join(FIELDS)} FROM audit_log ORDER BY id"):
        n += 1
        rid, prev_hash, h, values = r[0], r[1], r[2], tuple(r[3:])
        if h is None:
            problems.append({"id": rid, "problem": "has no seal (written outside the app, or its seal was removed)"})
        elif prev_hash != prev:
            problems.append({"id": rid, "problem": "doesn't follow the entry before it: an entry was deleted, "
                                                   "inserted or re-sealed"})
        elif not hmac.compare_digest(h, _digest(values, prev_hash)):
            problems.append({"id": rid, "problem": "was changed after it was written"})
        prev = h or prev
    ok = not problems
    msg = (f"All {n} audit entries are intact: each seal matches its content and the entry before it." if ok else
           f"{len(problems)} problem(s) in {n} entries. First: entry #{problems[0]['id']} {problems[0]['problem']}.")
    last = conn.execute("SELECT id, ts FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return {"ok": ok, "entries": n, "problems": problems[:20], "message": msg,
            "last_entry": {"id": last[0], "ts": last[1]} if last else None,
            "checked_at": datetime.now().isoformat(timespec="seconds")}


def log(conn, user: dict | None, event_type: str, *, question: str | None = None,
        sql: str | None = None, outcome: str | None = None, detail: dict | None = None,
        commit: bool = True) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log (ts, user_id, username, role, event_type, question, sql_text, outcome, detail_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"),
         user["id"] if user else None, user["username"] if user else None, user["role"] if user else None,
         event_type, question, sql, outcome, json.dumps(detail, default=str) if detail else None))
    seal(conn)                       # same transaction as the insert, which already holds the write lock
    if commit:
        conn.commit()
    return cur.lastrowid


def list_entries(conn, user: dict, limit: int = 100, event_type: str | None = None) -> list[dict]:
    """Owners see everyone's entries; everyone else sees only their own."""
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list = []
    if user["role"] != "owner":
        sql += " AND user_id = ?"
        args.append(user["id"])
    if event_type:
        sql += " AND event_type = ?"
        args.append(event_type)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json")) if d.get("detail_json") else None
        d.pop("prev_hash", None)
        out.append(d)
    return out
