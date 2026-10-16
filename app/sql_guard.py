"""Runs copilot-generated SQL safely. Security never depends on the LLM.

Layers (each one alone blocks the attack it targets):
  1. Shape check  - a single SELECT/WITH statement, no CTE may reuse a table/view name.
  2. Read-only    - connection opened with mode=ro and PRAGMA query_only=ON.
  3. Authorizer   - SQLite calls us for EVERY table/column the query reads, even
                    through views and sub-queries. Anything outside the role's
                    allow-list is denied, so a trainer can never read fee tables,
                    however the question or the SQL is phrased.
  4. Limits       - time limit (progress handler) and a row cap.
"""
import re
import sqlite3
import time

from . import config, db, rbac

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")

SAFE_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer",
                     "zipfile", "sqlar_compress", "sqlar_uncompress", "randomblob", "zeroblob"}


class GuardError(Exception):
    def __init__(self, code: str, message: str, blocked: list[str] | None = None):
        super().__init__(message)
        self.code = code            # bad_shape | forbidden | timeout | sql_error
        self.message = message
        self.blocked = blocked or []


# ---------------------------------------------------------------- tokenizer
def tokenize(sql: str) -> list[tuple[str, str]]:
    """Tokens as (kind, text): kind in ident | qident | string | number | sym."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                elif sql[j] == "'":
                    break
                else:
                    j += 1
            out.append(("string", sql[i:j + 1]))
            i = j + 1
        elif ch in "\"`[":
            close = {"\"": "\"", "`": "`", "[": "]"}[ch]
            j = sql.find(close, i + 1)
            j = n if j < 0 else j
            out.append(("qident", sql[i + 1:j]))
            i = j + 1
        elif ch.isalpha() or ch == "_":
            m = _IDENT.match(sql, i)
            out.append(("ident", m.group()))
            i = m.end()
        elif ch.isdigit():
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "."):
                j += 1
            out.append(("number", sql[i:j]))
            i = j
        else:
            out.append(("sym", ch))
            i += 1
    return out


def cte_names(tokens: list[tuple[str, str]]) -> list[str]:
    """Names defined as `name [(cols)] AS [NOT] [MATERIALIZED] (` - i.e. CTE names."""
    names = []
    for k, (kind, text) in enumerate(tokens):
        if kind != "ident" or text.upper() != "AS":
            continue
        j = k + 1
        while j < len(tokens) and tokens[j][1].upper() in ("NOT", "MATERIALIZED"):
            j += 1
        if j >= len(tokens) or tokens[j][1] != "(":
            continue
        p = k - 1                       # token before AS, skipping an optional column list
        if p >= 0 and tokens[p][1] == ")":
            depth = 0
            while p >= 0:
                if tokens[p][1] == ")":
                    depth += 1
                elif tokens[p][1] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                p -= 1
            p -= 1
        if p >= 0 and tokens[p][0] in ("ident", "qident"):
            names.append(tokens[p][1])
    return names


def _all_object_names(conn) -> set[str]:
    return {r[0].lower() for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _view_names(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}


def check_shape(sql: str, object_names: set[str]) -> str:
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise GuardError("bad_shape", "Empty query.")
    tokens = tokenize(sql)
    if not tokens or tokens[0][1].upper() not in ("SELECT", "WITH"):
        raise GuardError("bad_shape", "Only SELECT queries are allowed. The copilot never changes data directly.")
    if any(t == ("sym", ";") for t in tokens):
        raise GuardError("bad_shape", "Only one statement is allowed.")
    shadow = [n for n in cte_names(tokens) if n.lower() in object_names]
    if shadow:
        raise GuardError("forbidden", f"A sub-query may not reuse the name of a table or view ({', '.join(shadow)}).",
                         blocked=shadow)
    return sql


# ---------------------------------------------------------------- execution
def run_query(sql: str, user: dict) -> dict:
    """Validate and run one SELECT for this user. Raises GuardError when blocked."""
    role = user["role"]
    rbac.check_role(role)
    allowed = rbac.allowed_objects(role)

    conn = db.get_readonly_conn(user.get("trainer_id") if role == "trainer" else None)
    try:
        sql = check_shape(sql, _all_object_names(conn))
        views = _view_names(conn)
        ctes = {n.lower() for n in cte_names(tokenize(sql))}
        blocked: list[str] = []
        touched: set[str] = set()
        via_view: set[str] = set()      # base tables legitimately read through an allowed view

        def authorizer(action, arg1, arg2, dbname, source):
            if action not in SAFE_ACTIONS:
                blocked.append(f"operation #{action}")
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                name = (arg2 or "").lower()
                if name in BLOCKED_FUNCTIONS or (name.startswith("sqlite_") and name != "sqlite_version"):
                    blocked.append(f"function {arg2}")
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                table = arg1 or ""
                if table in allowed:
                    touched.add(table)
                    return sqlite3.SQLITE_OK
                # A real, allowed view reading its own base tables
                if source and source in views and source in allowed:
                    via_view.add(table)
                    return sqlite3.SQLITE_OK
                # A recursive CTE reading itself (CTE names can't shadow real objects)
                if table.lower() in ctes:
                    return sqlite3.SQLITE_OK
                # SQLite's "table used, no column read" check for a flattened allowed view
                if arg2 == "" and table in via_view:
                    return sqlite3.SQLITE_OK
                blocked.append(table)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        deadline = time.monotonic() + config.QUERY_TIMEOUT_MS / 1000
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
        conn.set_authorizer(authorizer)
        started = time.perf_counter()
        try:
            cur = conn.execute(sql)
            rows = cur.fetchmany(config.MAX_ROWS + 1)
        except sqlite3.DatabaseError as e:
            msg = str(e)
            if blocked:
                names = sorted({b for b in blocked if b})
                raise GuardError("forbidden",
                                 f"Your role ({role}) is not allowed to read: {', '.join(names)}. "
                                 f"{rbac.ROLE_RESTRICTIONS[role]}", blocked=names)
            if "interrupted" in msg:
                raise GuardError("timeout", f"Query took longer than {config.QUERY_TIMEOUT_MS} ms and was stopped.")
            raise GuardError("sql_error", msg)
        except sqlite3.ProgrammingError as e:
            raise GuardError("bad_shape", str(e))
        columns = [d[0] for d in cur.description] if cur.description else []
        truncated = len(rows) > config.MAX_ROWS
        rows = rows[: config.MAX_ROWS]
        return {
            "sql": sql,
            "columns": columns,
            "rows": [dict(zip(columns, tuple(r))) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "objects_read": sorted(touched),
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    finally:
        conn.close()
