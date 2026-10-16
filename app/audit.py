"""Append-only audit log of everything the copilot and its users do."""
import json
from datetime import datetime


def log(conn, user: dict | None, event_type: str, *, question: str | None = None,
        sql: str | None = None, outcome: str | None = None, detail: dict | None = None,
        commit: bool = True) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log (ts, user_id, username, role, event_type, question, sql_text, outcome, detail_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"),
         user["id"] if user else None, user["username"] if user else None, user["role"] if user else None,
         event_type, question, sql, outcome, json.dumps(detail, default=str) if detail else None))
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
        out.append(d)
    return out
