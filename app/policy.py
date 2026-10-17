"""Checks around the copilot that never depend on the AI.

Before the AI sees a question:
  * SQL typed into the chat is not run (the copilot writes and checks its own queries).
  * Requests to delete or erase records are refused (nothing can delete data).
  * A trainer asking about another trainer's batch is refused (own batches only).
After an answer is made:
  * A trainer never gets an answer to a fee question, even one the AI answered from non-fee data
    (e.g. "rank my students by how fast they pay" answered from attendance).
  * A counselor never gets collection totals or bank data.
  * Answers always carry the names and numbers they are about (see enrich), so a short AI summary
    never hides a row.
The database guard (sql_guard.py) still blocks anything these checks miss.
"""
import re

SQL_IN_CHAT = re.compile(
    r"\bselect\s+(?:\*|distinct\b|[\w.]+\s*(?:,|\bfrom\b))|\b(?:drop|truncate|alter)\s+table\b|\binsert\s+into\b"
    r"|\bdelete\s+from\b|\bupdate\s+\w+\s+set\b|;\s*--|'\s*\)\s*;|\bunion\s+(?:all\s+)?select\b|\bpragma\b", re.I)
DESTRUCTIVE = re.compile(r"\b(delete|erase|wipe|purge|truncate)\b|\bdrop\s+(table|database|all|every|the\s+(table|students|"
                         r"records))\b|\bremove\s+(all|every|the)?\s*(students?|records?|data|payments?|emis?|batch(es)?|"
                         r"entries)\b", re.I)
FEE_WORDS = re.compile(r"\b(fee|fees|emi|emis|instal\w*|install?ments?|payments?|paid|pay|pays|payer|payers|paying|owe|owes|"
                       r"owed|owing|dues|money|rupees?|amount|balance|outstanding|defaulters?|collection)\b|₹|फीस|शुल्क",
                       re.I)
COLLECTION_WORDS = re.compile(r"\b(collect\w*|revenue|income|earnings?|bank|utr|deposits?|transactions?)\b"
                              r"|\b(payments?|money|amount|fees?)\s+received\b|\breceived\s+(payments?|money|fees?)\b", re.I)
CHANGE_WORDS = re.compile(r"\b(set|mark|update|change|delete|remove|waive|make|cancel|edit|modify|record|add|zero|"
                          r"reset|clear|write off)\b", re.I)
GENERIC = {"morning", "evening", "weekend", "saturday", "sunday", "spring", "summer", "winter", "new", "full", "stack",
           "data", "science", "batch", "course", "and", "the", "with", "english"}

SQL_REFUSAL = ("Running SQL typed into the chat is not allowed, so no query was run. Ask in plain language instead "
               "(for example \"fee status of Rohan Gupta\"); the copilot writes and checks its own read-only queries.")
DELETE_REFUSAL = ("I can't delete or erase records: the copilot is read-only and never changes data by itself. Changes "
                  "such as marking an EMI paid or sending a reminder are prepared as a preview that a person must "
                  "approve, and deleting students is not possible at all.")
TRAINER_FEE_REFUSAL = ("Trainers can't see fees, EMIs or payments, so I can't answer that (not even without amounts). "
                       "I can show attendance, assignments and recordings for your own batches, for example "
                       "\"Which students in B01 have missed 3 or more classes?\"")
COUNSELOR_MONEY_REFUSAL = ("Counselors can't see payments received, collection totals or the bank statement; that is for "
                           "the owner and accounts staff. I can show EMIs that are due or overdue.")
APPROVAL_NOTE = " Anything that changes data is prepared as a preview that a person must approve."
RECONCILE_NOTE = (" This check is read-only: nothing is posted to the ledger until a person records or approves each "
                  "payment.")


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).lower().replace(",", "").replace("₹", "").replace("rs.", "rs")).strip()


# ---------------------------------------------------------------- before the AI
def precheck(conn, user: dict, question: str) -> tuple[str, str] | None:
    """(rule, message) when the question must be refused without asking the AI."""
    if SQL_IN_CHAT.search(question):
        return "sql_in_chat", SQL_REFUSAL
    if DESTRUCTIVE.search(question):
        return "delete_request", DELETE_REFUSAL
    if user["role"] == "trainer":
        other = other_batch_mention(conn, user, question)
        if other:
            own = ", ".join(f"{r['code']} {r['short']}" for r in _own_batches(conn, user))
            return "other_trainers_batch", (f"You can only see your own batches ({own}). {other} belongs to another "
                                            f"trainer, so its data is not available to you.")
    return None


def _short(name: str) -> str:
    if " - " in name:
        return name.split(" ")[0] + " " + name.split(" - ", 1)[1].replace("(NEW)", "").strip()
    return name


def _own_batches(conn, user) -> list[dict]:
    return [{"code": r["code"], "short": _short(r["name"])} for r in
            conn.execute("SELECT b.code, COALESCE(b.name, c.name, b.code) AS name FROM batches b "
                         "LEFT JOIN courses c ON c.id = b.course_id WHERE b.trainer_id = ? ORDER BY b.code",
                         (user.get("trainer_id"),))]


def other_batch_mention(conn, user: dict, question: str) -> str | None:
    """The other trainer / batch a trainer's question names, if any."""
    q = " " + question.lower() + " "
    rows = conn.execute("SELECT b.code, COALESCE(b.name, c.name, b.code) AS name, b.trainer_id, t.name AS trainer "
                        "FROM batches b JOIN trainers t ON t.id = b.trainer_id "
                        "LEFT JOIN courses c ON c.id = b.course_id").fetchall()
    own_words = set()
    for r in rows:
        if r["trainer_id"] == user.get("trainer_id"):
            own_words |= set(re.findall(r"[a-z]+", r["name"].lower()))
    for r in rows:
        if r["trainer_id"] == user.get("trainer_id"):
            continue
        if re.search(rf"\b{r['code'].lower()}\b", q):
            return f"Batch {r['code']}"
        trainer = r["trainer"].lower()
        first = trainer.split()[0]
        if trainer in q or re.search(rf"\b{first}\s*(sir|ma'?am|madam|mam|ji)\b|\b{first}(?:'s|’s)\b", q):
            return f"{r['trainer']}'s batch ({r['code']})"
        words = set(re.findall(r"[a-z]+", r["name"].lower())) - own_words - GENERIC
        hit = next((w for w in words if len(w) >= 3 and re.search(rf"\b{w}\b", q)), None)
        if hit:
            return f"The {_short(r['name'])} batch ({r['code']})"
    return None


# ---------------------------------------------------------------- after the answer
def postcheck(user: dict, question: str, out: dict) -> tuple[str, str] | None:
    """(rule, message) when a finished answer must be withheld for this role."""
    if out.get("type") in ("blocked", "refusal"):
        return None                                   # already refused (by the database, a check or the AI)
    if user["role"] == "trainer" and FEE_WORDS.search(question):
        return "trainer_fee_question", TRAINER_FEE_REFUSAL
    if user["role"] == "counselor" and COLLECTION_WORDS.search(question) and out.get("type") != "action_preview":
        return "counselor_collections", COUNSELOR_MONEY_REFUSAL
    return None


def notes(question: str, out: dict) -> str:
    """Short notes added to the answer text."""
    extra = ""
    if out.get("type") in ("refusal", "error") and CHANGE_WORDS.search(question) and "approv" not in out.get("answer", ""):
        extra += APPROVAL_NOTE
    if out.get("type") == "answer" and re.search(r"reconcil", question, re.I):
        extra += RECONCILE_NOTE
    return extra


# ---------------------------------------------------------------- answers carry their facts
_SKIP = re.compile(r"(^id$|_id$|phone|^url$|email)")


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _label(columns: list[str], rows: list[dict]) -> str | None:
    texty = [c for c in columns if not _SKIP.search(c) and any(isinstance(r.get(c), str) for r in rows)]
    for pref in ("short_name", "student_name", "name", "trainer_name", "description"):
        if pref in texty:
            return pref
    named = [c for c in texty if c.endswith("name")]
    return (named or texty or [None])[0]


def _row_text(columns: list[str], row: dict, label: str | None) -> str:
    vals = [_fmt(row[c]) for c in columns if c != label and not _SKIP.search(c) and row.get(c) not in (None, "")]
    head = _fmt(row[label]) if label and row.get(label) not in (None, "") else ""
    return (f"{head} ({', '.join(vals)})" if head else ", ".join(f"{c.replace('_', ' ')}: {_fmt(row[c])}"
            for c in columns if not _SKIP.search(c) and row.get(c) is not None)) if vals else head


def enrich(answer: str, columns: list[str], rows: list[dict], row_count: int) -> str:
    """Add the rows (up to 12) to a summary that leaves some of them out."""
    if not rows or row_count > 12:
        return answer
    t = norm(answer)
    label = _label(columns, rows)
    missing = False
    if label:
        missing = any(norm(_fmt(r[label])) not in t for r in rows if r.get(label) not in (None, ""))
    if not missing and row_count <= 3:                    # few rows: every value should be in the answer
        for r in rows:
            for c in columns:
                v = r.get(c)
                if v is None or _SKIP.search(c) or (isinstance(v, str) and len(v) < 3):
                    continue
                if norm(_fmt(v)) not in t:
                    missing = True
    if not missing:
        return answer
    details = "; ".join(_row_text(columns, r, label) for r in rows)
    sep = "" if answer.rstrip().endswith((".", "!", "?", ":")) else "."
    return f"{answer.rstrip()}{sep} Details ({row_count} row{'s' if row_count != 1 else ''}): {details}."
