"""Human-approved actions. Nothing that changes data or sends a message runs
until a person with the right role approves the preview.

Action types
  send_whatsapp         payload.items = [{student_id, name, phone, message}]
  mark_instalment_paid  payload.items = [{instalment_id, student_name, amount, balance, due_date}]
  apply_timetable       payload = timetable proposal from the planner
"""
import hashlib
import json
import re
from datetime import date, datetime

from . import audit, config, rbac, timetable

MAX_ITEMS = 50
# Besides their own proposals, a role also sees these proposals from anyone, because it may read all the
# data inside them (e.g. a parent alert made when a trainer saves attendance waits for a counselor).
# Accounts staff can't read attendance, so they don't see other people's messages. The owner sees everything.
SHARED_QUEUE = {"counselor": {"send_whatsapp", "issue_certificate"}}


def _visible_types(role: str) -> list[str]:
    return sorted(SHARED_QUEUE.get(role, set()) & set(rbac.ROLES.get(role, {}).get("actions", [])))


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# ---------------------------------------------------------------- helpers
def _pretty(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).strftime("%d %b %Y")
    if isinstance(value, int) and value >= 1000:
        return f"{value:,}"
    return "" if value is None else str(value)


def fill_template(template: str, row: dict) -> str:
    lower = {k.lower(): v for k, v in row.items()}
    return re.sub(r"\{(\w+)\}", lambda m: _pretty(lower[m.group(1).lower()])
                  if m.group(1).lower() in lower else m.group(0), template)


def _first(row: dict, *names):
    lower = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n in lower and lower[n] not in (None, ""):
            return lower[n]
    return None


def _create(conn, user, action_type: str, title: str, payload: dict) -> dict:
    cur = conn.execute(
        "INSERT INTO pending_actions (created_at, created_by, action_type, title, payload_json, status) "
        "VALUES (?,?,?,?,?, 'pending')",
        (datetime.now().isoformat(timespec="seconds"), user["id"], action_type, title, json.dumps(payload)))
    action_id = cur.lastrowid
    audit.log(conn, user, "action_proposed", outcome="pending",
              detail={"action_id": action_id, "action_type": action_type, "title": title,
                      "items": len(payload.get("items", payload.get("changes", [])))}, commit=False)
    conn.commit()
    return get(conn, user, action_id)


# ---------------------------------------------------------------- proposals
def propose_messages(conn, user, rows: list[dict], template: str, title: str | None = None) -> dict:
    if not rbac.can_do(user["role"], "send_whatsapp"):
        raise ActionError(f"Your role ({user['role']}) cannot send messages.", 403)
    items, skipped, seen, duplicates = [], [], set(), 0
    for row in rows:
        if len(items) >= MAX_ITEMS:
            break
        phone = _first(row, "phone", "student_phone", "mobile", "guardian_phone")
        name = _first(row, "name", "student_name") or "Student"
        if not phone:
            skipped.append(name)
            continue
        key = _first(row, "student_id") or phone          # one message per student, even if several rows match
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        items.append({"student_id": _first(row, "student_id", "id"), "name": name, "phone": phone,
                      "message": fill_template(template, row)})
    if not items:
        raise ActionError("No recipients with a phone number were found, so there is nothing to send.")
    payload = {"channel": "whatsapp", "template": template, "items": items, "skipped_no_phone": skipped,
               "merged_duplicates": duplicates, "capped_at": MAX_ITEMS if len(rows) - duplicates > MAX_ITEMS else None}
    return _create(conn, user, "send_whatsapp", title or f"WhatsApp reminder to {len(items)} students", payload)


def propose_mark_paid(conn, user, rows: list[dict], title: str | None = None) -> dict:
    if not rbac.can_do(user["role"], "mark_instalment_paid"):
        raise ActionError(f"Your role ({user['role']}) cannot change fee records.", 403)
    items = []
    for row in rows[:MAX_ITEMS]:
        iid = _first(row, "instalment_id", "id")
        if iid is None:
            continue
        inst = conn.execute("SELECT i.id, i.amount, i.paid_amount, i.status, i.due_date, s.name "
                            "FROM instalments i JOIN fee_plans fp ON fp.id=i.fee_plan_id "
                            "JOIN enrollments e ON e.id=fp.enrollment_id JOIN students s ON s.id=e.student_id "
                            "WHERE i.id=?", (iid,)).fetchone()
        if inst and inst["status"] != "paid":
            items.append({"instalment_id": inst["id"], "student_name": inst["name"], "due_date": inst["due_date"],
                          "amount": inst["amount"], "balance": inst["amount"] - inst["paid_amount"]})
    if not items:
        raise ActionError("No unpaid instalments matched, so there is nothing to mark as paid.")
    return _create(conn, user, "mark_instalment_paid",
                   title or f"Mark {len(items)} instalment(s) as paid", {"items": items, "method": "UPI"})


def slots_fingerprint(slots: list[dict]) -> str:
    key = sorted((s["batch_id"], s["trainer_id"], s["room_id"] or 0, s["day_of_week"], s["start_hour"])
                 for s in slots)
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:12]


def propose_timetable(conn, user, result: dict, title: str, leaves: list[dict] | None = None,
                      new_batch: dict | None = None) -> dict:
    if not rbac.can_do(user["role"], "apply_timetable"):
        raise ActionError(f"Your role ({user['role']}) cannot change the timetable.", 403)
    week_start = date.fromisoformat(result["week_start"])
    payload = dict(result)
    payload["base_fingerprint"] = slots_fingerprint(timetable.load_slots(conn, week_start))
    payload["leaves"] = leaves or []
    payload["new_batch"] = new_batch
    return _create(conn, user, "apply_timetable", title, payload)


# ---------------------------------------------------------------- reading
def _row_to_action(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d.pop("payload_json"))
    d["result"] = json.loads(d.pop("result_json")) if d.get("result_json") else None
    return d


def list_actions(conn, user, status: str | None = None) -> list[dict]:
    sql = ("SELECT a.*, u.display_name AS created_by_name FROM pending_actions a "
           "JOIN users u ON u.id = a.created_by WHERE 1=1")
    args: list = []
    if user["role"] != "owner":
        shared = _visible_types(user["role"])
        sql += f" AND (a.created_by = ? OR a.action_type IN ({','.join('?' * len(shared)) or 'NULL'}))"
        args += [user["id"], *shared]
    if status:
        sql += " AND a.status = ?"
        args.append(status)
    sql += " ORDER BY a.id DESC LIMIT 100"
    return [_row_to_action(r) for r in conn.execute(sql, args)]


def get(conn, user, action_id: int) -> dict:
    r = conn.execute("SELECT a.*, u.display_name AS created_by_name FROM pending_actions a "
                     "JOIN users u ON u.id = a.created_by WHERE a.id=?", (action_id,)).fetchone()
    if r is None or (user["role"] != "owner" and r["created_by"] != user["id"]
                     and r["action_type"] not in _visible_types(user["role"])):
        raise ActionError("Action not found.", 404)
    return _row_to_action(r)


# ---------------------------------------------------------------- deciding
def _decidable(conn, user, action_id: int) -> dict:
    a = get(conn, user, action_id)
    if a["status"] != "pending":
        raise ActionError(f"This action is already {a['status']}.", 409)
    if not rbac.can_do(user["role"], a["action_type"]):
        raise ActionError(f"Your role ({user['role']}) cannot approve '{a['action_type']}'.", 403)
    return a


def reject(conn, user, action_id: int, reason: str | None = None) -> dict:
    _decidable(conn, user, action_id)
    conn.execute("UPDATE pending_actions SET status='rejected', decided_by=?, decided_at=?, result_json=? "
                 "WHERE id=? AND status='pending'",
                 (user["id"], datetime.now().isoformat(timespec="seconds"),
                  json.dumps({"reason": reason or ""}), action_id))
    audit.log(conn, user, "action_rejected", outcome="rejected",
              detail={"action_id": action_id, "reason": reason}, commit=False)
    conn.commit()
    return get(conn, user, action_id)


def approve(conn, user, action_id: int, edits: dict | None = None) -> dict:
    a = _decidable(conn, user, action_id)
    edits = edits or {}
    now = datetime.now().isoformat(timespec="seconds")
    try:
        conn.execute("BEGIN IMMEDIATE")
        # claim it first, so a double click can never run an action twice
        claimed = conn.execute("UPDATE pending_actions SET status='approved', decided_by=?, decided_at=? "
                               "WHERE id=? AND status='pending'", (user["id"], now, action_id)).rowcount
        if not claimed:
            raise ActionError("This action was already decided.", 409)
        from . import certificates, entry, leave  # data typed in through forms or the copilot
        runners = {"send_whatsapp": _run_messages, "mark_instalment_paid": _run_mark_paid,
                   "apply_timetable": _run_timetable, "issue_certificate": certificates.run,
                   "request_leave": leave.run, **entry.RUNNERS}
        entry.check_scope(conn, user, a)
        runner = runners[a["action_type"]]
        result = runner(conn, a, edits)
        conn.execute("UPDATE pending_actions SET result_json=? WHERE id=?", (json.dumps(result), action_id))
        audit.log(conn, user, "action_approved", outcome="executed",
                  detail={"action_id": action_id, "action_type": a["action_type"], **result.get("summary", {})},
                  commit=False)
        conn.commit()
        if a["action_type"] == "send_whatsapp":
            _deliver_queued(conn, user, action_id)   # network calls happen after the commit, never inside it
        if a["action_type"] == "mark_attendance":
            _follow_up(conn, user, action_id, entry.parent_alert)
        if a["action_type"] == "request_leave":
            _follow_up(conn, user, action_id, leave.replan)
    except ActionError:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        conn.execute("UPDATE pending_actions SET status='failed', decided_by=?, decided_at=?, result_json=? "
                     "WHERE id=? AND status='pending'", (user["id"], now, json.dumps({"error": str(e)}), action_id))
        audit.log(conn, user, "action_failed", outcome="failed", detail={"action_id": action_id, "error": str(e)})
        raise ActionError(f"The action failed and nothing was changed: {e}", 500) from e
    return get(conn, user, action_id)


def _follow_up(conn, user, action_id: int, make) -> None:
    """After an approved action is saved, prepare the next step as a new preview (never sent by itself)."""
    try:
        nxt = make(conn, user, get(conn, user, action_id))
    except ActionError as e:
        audit.log(conn, user, "follow_up_skipped", outcome="skipped", detail={"action_id": action_id, "why": e.message})
        return
    if not nxt:
        return
    row = conn.execute("SELECT result_json FROM pending_actions WHERE id=?", (action_id,)).fetchone()
    result = json.loads(row["result_json"] or "{}")
    result["follow_up_action_id"] = nxt["action"]["id"]
    result["message"] = (result.get("message", "Saved.") + " " + nxt["note"]).strip()
    conn.execute("UPDATE pending_actions SET result_json=? WHERE id=?", (json.dumps(result), action_id))
    conn.commit()


def _run_messages(conn, a: dict, edits: dict) -> dict:
    exclude = {int(i) for i in edits.get("exclude", [])}
    overrides = {int(k): v for k, v in (edits.get("items") or {}).items()}
    sent = []
    for i, item in enumerate(a["payload"]["items"]):
        if i in exclude:
            continue
        body = (overrides.get(i) or {}).get("message", item["message"])
        conn.execute("INSERT INTO messages_outbox (action_id, channel, student_id, to_phone, body, status, created_at) "
                     "VALUES (?,?,?,?,?, 'queued', ?)", (a["id"], "whatsapp", item.get("student_id"), item["phone"],
                                                         body, datetime.now().isoformat(timespec="seconds")))
        sent.append({"name": item["name"], "phone": item["phone"], "status": "queued"})
    return {"sent": sent, "summary": {"messages": len(sent), "excluded": len(exclude)}}


def _deliver_queued(conn, user, action_id: int) -> None:
    """Send the approved messages (really, or simulated: see app/whatsapp.py) and record each result."""
    from . import whatsapp
    # trust is decided by the phone number itself: only a number staff typed in (student's or parent's) is "real"
    rows = conn.execute("SELECT o.id, o.to_phone, o.body, s.name, EXISTS (SELECT 1 FROM students t "
                        "WHERE (t.phone = o.to_phone OR t.guardian_phone = o.to_phone) AND t.real_contact = 1) "
                        "AS real_contact FROM messages_outbox o "
                        "LEFT JOIN students s ON s.id = o.student_id WHERE o.action_id=? AND o.status='queued' "
                        "ORDER BY o.id", (action_id,)).fetchall()
    statuses, links = [], []
    for r in rows:
        trusted = bool(r["real_contact"])                 # added in the app: the number is real
        status = whatsapp.send(r["to_phone"], r["body"], r["name"] or "", trusted)
        conn.execute("UPDATE messages_outbox SET status=? WHERE id=?", (status, r["id"]))
        statuses.append(status)
        # not sent automatically? a person can still send it in one tap (same who-may-receive rule)
        links.append(None if status.startswith("sent") else whatsapp.link(r["to_phone"], r["body"], r["name"] or "", trusted))
        conn.commit()
    row = conn.execute("SELECT result_json FROM pending_actions WHERE id=?", (action_id,)).fetchone()
    result = json.loads(row["result_json"] or "{}")
    for item, status, wa in zip(result.get("sent", []), statuses, links):
        item["status"] = status
        if wa:
            item["wa_link"], item["wa_to"] = wa["url"], wa["to"]
    result["delivery"] = whatsapp.mode()
    conn.execute("UPDATE pending_actions SET result_json=? WHERE id=?", (json.dumps(result), action_id))
    sent = sum(s.startswith("sent") for s in statuses)
    failed = sum(s.startswith("failed") for s in statuses)
    ready = sum(s.startswith("ready") for s in statuses)
    audit.log(conn, user, "whatsapp_delivery",
              outcome="sent" if sent and not failed else "failed" if failed else "ready" if ready else "simulated",
              detail={"action_id": action_id, "sent": sent, "failed": failed, "ready_to_send": ready,
                      "simulated": len(statuses) - sent - failed - ready, "provider": result["delivery"]["provider"]},
              commit=False)
    conn.commit()


def _run_mark_paid(conn, a: dict, edits: dict) -> dict:
    method = edits.get("method") or a["payload"].get("method", "UPI")
    if method not in ("UPI", "bank_transfer", "cash", "card"):
        raise ActionError("method must be UPI, bank_transfer, cash or card.")
    today = config.today().isoformat()
    done, links = [], []
    for item in a["payload"]["items"]:
        inst = conn.execute("SELECT i.*, e.student_id FROM instalments i JOIN fee_plans fp ON fp.id=i.fee_plan_id "
                            "JOIN enrollments e ON e.id=fp.enrollment_id WHERE i.id=?",
                            (item["instalment_id"],)).fetchone()
        if inst is None or inst["status"] == "paid":
            continue
        balance = inst["amount"] - inst["paid_amount"]
        conn.execute("UPDATE instalments SET paid_amount=amount, status='paid', paid_on=? WHERE id=?",
                     (today, inst["id"]))
        pid = conn.execute("INSERT INTO payments (instalment_id, student_id, amount, paid_on, method, reference) "
                           "VALUES (?,?,?,?,?,?)", (inst["id"], inst["student_id"], balance, today, method,
                                                    f"APPROVED-ACTION-{a['id']}")).lastrowid
        from . import receipts
        rc = receipts.issue(conn, pid)
        links += rc["links"]
        done.append({"instalment_id": inst["id"], "student_name": item["student_name"], "amount": balance,
                     "receipt_no": rc["receipt_no"]})
    return {"marked_paid": done, "links": links, "summary": {"instalments": len(done)}}


def _run_timetable(conn, a: dict, edits: dict) -> dict:
    p = a["payload"]
    week_start = date.fromisoformat(p["week_start"])
    if slots_fingerprint(timetable.load_slots(conn, week_start)) != p["base_fingerprint"]:
        raise ActionError("The timetable changed after this preview was made. Please re-plan and approve the new preview.", 409)
    new_id = None
    if p.get("new_batch"):
        nb = p["new_batch"]
        if conn.execute("SELECT 1 FROM batches WHERE code=?", (nb["code"],)).fetchone():
            raise ActionError(f"A batch called {nb['code']} already exists.", 409)
        course = conn.execute("SELECT id FROM courses WHERE code=?", (nb["course_code"],)).fetchone()
        mine = sorted((s for s in p["slots"] if s["batch_id"] < 0), key=lambda s: s["day_of_week"])
        status = "upcoming" if nb["start_date"] > config.today().isoformat() else "active"
        same_time = bool(mine) and len({(s["start_hour"], s["end_hour"]) for s in mine}) == 1
        cur = conn.execute(
            "INSERT INTO batches (code, name, course_id, trainer_id, mode, time_window, sessions_per_week, session_hours, "
            "online_seat_limit, days, start_time, end_time, room_id, start_date, end_date, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nb["code"], nb.get("name"), course["id"], nb["trainer_id"], nb["mode"], nb["time_window"],
             nb["sessions_per_week"], nb.get("session_hours", 2), nb.get("online_seat_limit", 0),
             ",".join(timetable.DAY_NAMES[s["day_of_week"]] for s in mine) or None,
             timetable.hm(mine[0]["start_hour"]) if same_time else None,      # only if every class
             timetable.hm(mine[0]["end_hour"]) if same_time else None,        # starts at the same time
             mine[0]["room_id"] if mine else None, nb["start_date"], nb["end_date"], status))
        new_id = cur.lastrowid
    for lv in p.get("leaves", []):
        conn.execute("INSERT INTO trainer_leaves (trainer_id, leave_date, reason) SELECT ?,?,? WHERE NOT EXISTS "
                     "(SELECT 1 FROM trainer_leaves WHERE trainer_id=? AND leave_date=?)",
                     (lv["trainer_id"], lv["leave_date"], lv.get("reason"), lv["trainer_id"], lv["leave_date"]))
    slots = [dict(s, batch_id=new_id if s["batch_id"] < 0 else s["batch_id"]) for s in p["slots"]]
    timetable.save_slots(conn, week_start, slots)
    return {"applied_slots": len(slots), "new_batch_id": new_id,
            "summary": {"changes": len(p.get("changes", [])), "slots": len(slots)}}
