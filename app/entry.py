"""Typing data in: new students, payments, attendance, assignments and marks.

Every form (and every copilot request to add data) only creates a PREVIEW, stored as a
pending action. Nothing is written until a person confirms it, and confirming checks
everything again (seats may have filled up, the EMI may have been paid meanwhile).

Who may do what (see rbac.ROLES):
  add_student      owner, counselor
  record_payment   owner, accounts
  mark_attendance  owner, trainer (own batches only)
  save_assignment  owner, trainer (own batches only)
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from . import config, db, rbac, timetable
from .actions import ActionError, _create

ENTRY_ACTIONS = ("add_student", "record_payment", "mark_attendance", "save_assignment")
METHODS = {"upi": "UPI", "bank_transfer": "bank_transfer", "bank transfer": "bank_transfer", "neft": "bank_transfer",
           "imps": "bank_transfer", "rtgs": "bank_transfer", "bank": "bank_transfer", "cash": "cash", "card": "card"}
METHOD_LABEL = {"UPI": "UPI", "bank_transfer": "Bank transfer", "cash": "Cash", "card": "Card"}


# ---------------------------------------------------------------- small helpers
def _rupees(n) -> str:
    return f"₹{int(n or 0):,}"


def _need(user: dict, action_type: str) -> None:
    if not rbac.can_do(user["role"], action_type):
        raise ActionError(f"Your role ({user['role']}) cannot do this. {rbac.ROLE_RESTRICTIONS[user['role']]}", 403)


def _date(value, default: date | None = None, field: str = "date") -> date:
    if value in (None, ""):
        if default is None:
            raise ActionError(f"{field} is required (YYYY-MM-DD).")
        return default
    v = str(value).strip().lower()
    if v == "today":
        return config.today()
    if v == "yesterday":
        return config.today() - timedelta(days=1)
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise ActionError(f"{field} must be a date like 2026-10-16.")


def normalize_phone(value: str | None, field: str = "phone", required: bool = True) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        if required:
            raise ActionError(f"{field} is required.")
        return None
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise ActionError(f"{field} must be a 10-digit Indian mobile number starting with 6-9 (got {value!r}).")
    return "+91" + digits


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower().strip(), (b or "").lower().strip()).ratio()


def _batch(conn, user: dict, ref) -> dict:
    """A batch by id or code. Trainers only ever get their own batches."""
    if ref in (None, ""):
        raise ActionError("Choose a batch.")
    row = conn.execute("""
        SELECT b.*, c.code AS course_code, c.name AS course_name, c.total_fee, c.emi_count, t.name AS trainer_name,
               r.name AS room_name, r.capacity AS room_capacity,
               (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
                    AND e.learning_mode='online') AS online_students,
               (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
                    AND e.learning_mode='classroom') AS classroom_students
        FROM batches b JOIN courses c ON c.id=b.course_id JOIN trainers t ON t.id=b.trainer_id
        LEFT JOIN rooms r ON r.id=b.room_id
        WHERE b.id = ? OR upper(b.code) = upper(?)""",
                       (ref if str(ref).isdigit() else -1, str(ref).strip())).fetchone()
    if row is None:
        raise ActionError(f"No batch called {ref}.", 404)
    b = dict(row)
    if user["role"] == "trainer" and b["trainer_id"] != user["trainer_id"]:
        raise ActionError(f"{b['code']} is not your batch. Trainers can only update their own batches.", 403)
    return b


def _roster(conn, batch_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT s.id AS student_id, s.name, e.learning_mode FROM enrollments e JOIN students s ON s.id=e.student_id
        WHERE e.batch_id=? AND e.status='active' ORDER BY s.name""", (batch_id,))]


def match_names(names: list[str], roster: list[dict]) -> list[dict]:
    """Map names typed by a person (or the AI) to students in the roster; unknown names are an error."""
    out = []
    for n in names or []:
        n = str(n).strip()
        if not n:
            continue
        exact = [s for s in roster if s["name"].lower() == n.lower()]
        part = [s for s in roster if n.lower() in s["name"].lower()]
        best = max(roster, key=lambda s: _similar(n, s["name"]), default=None)
        pick = exact[0] if exact else part[0] if len(part) == 1 else \
            best if best and _similar(n, best["name"]) >= 0.8 else None
        if pick is None:
            raise ActionError(f"No student called '{n}' in this batch. Students: {', '.join(s['name'] for s in roster)}.")
        out.append(pick)
    return out


def _preview(summary: str, fields: list, table: dict | None = None, checks: list | None = None,
             warnings: list | None = None) -> dict:
    return {"summary": summary, "fields": [[str(k), "" if v is None else str(v)] for k, v in fields],
            "table": table, "checks": checks or [], "warnings": warnings or []}


# ---------------------------------------------------------------- read helpers for the forms
def options(conn, user: dict) -> dict:
    role = user["role"]
    forms = [a for a in ENTRY_ACTIONS if rbac.can_do(role, a)]
    where, args = "b.status IN ('active','upcoming')", []
    if role == "trainer":
        where += " AND b.trainer_id = ?"
        args.append(user["trainer_id"])
    batches = [dict(r) for r in conn.execute(f"""
        SELECT b.id, b.code, COALESCE(b.name, c.name) AS name, c.name AS course_name, b.mode, b.status, b.days,
               b.start_time, b.end_time, b.start_date, b.end_date, b.online_seat_limit, t.name AS trainer_name,
               r.name AS room_name, r.capacity AS room_capacity,
               (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
                    AND e.learning_mode='online') AS online_students,
               (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
                    AND e.learning_mode='classroom') AS classroom_students
        FROM batches b JOIN courses c ON c.id=b.course_id JOIN trainers t ON t.id=b.trainer_id
        LEFT JOIN rooms r ON r.id=b.room_id WHERE {where} ORDER BY b.code""", args)]
    if role == "trainer":                      # trainers see no fee or seat-business details
        for b in batches:
            b.pop("room_capacity", None)
    if role in ("trainer", "owner"):                       # leave requests (see leave.py)
        forms.append("request_leave")
    out = {"forms": forms, "today": config.today().isoformat(), "batches": batches}
    if role == "owner":
        out["trainers"] = [dict(r) for r in conn.execute("SELECT id, name FROM trainers ORDER BY name")]
    if "add_student" in forms:
        out["courses"] = [dict(r) for r in conn.execute(
            "SELECT code, name, total_fee, emi_count FROM courses ORDER BY code")]
    return out


def roster(conn, user: dict, batch_ref, day=None) -> dict:
    _need(user, "mark_attendance")
    b = _batch(conn, user, batch_ref)
    d = _date(day, config.today())
    sess = conn.execute("SELECT id, topic FROM class_sessions WHERE batch_id=? AND session_date=? ORDER BY id LIMIT 1",
                        (b["id"], d.isoformat())).fetchone()
    marks = {}
    if sess:
        marks = {r["student_id"]: r["status"] for r in conn.execute(
            "SELECT student_id, status FROM attendance WHERE session_id=?", (sess["id"],))}
    usual = timetable.DAY_NAMES[d.weekday()] in (b["days"] or "")
    return {"batch": {"id": b["id"], "code": b["code"], "name": b["name"], "days": b["days"],
                      "start_time": b["start_time"], "end_time": b["end_time"]},
            "date": d.isoformat(), "usual_day": usual if b["days"] else None,
            "session_id": sess["id"] if sess else None, "topic": sess["topic"] if sess else None,
            "students": [dict(s, status=marks.get(s["student_id"])) for s in _roster(conn, b["id"])]}


def assignments(conn, user: dict, batch_ref) -> dict:
    _need(user, "save_assignment")
    b = _batch(conn, user, batch_ref)
    items = [dict(r) for r in conn.execute(
        "SELECT a.id, a.title, a.due_date, (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id) AS submitted "
        "FROM assignments a WHERE a.batch_id=? ORDER BY a.due_date, a.id", (b["id"],))]
    scores: dict = {}
    for r in conn.execute("SELECT s.assignment_id, s.student_id, s.score, s.status FROM submissions s "
                          "JOIN assignments a ON a.id=s.assignment_id WHERE a.batch_id=?", (b["id"],)):
        scores.setdefault(str(r["assignment_id"]), {})[str(r["student_id"])] = {"score": r["score"], "status": r["status"]}
    return {"batch": {"id": b["id"], "code": b["code"], "name": b["name"]}, "assignments": items,
            "students": _roster(conn, b["id"]), "scores": scores}


def open_instalments(conn, user: dict, q: str = "") -> list[dict]:
    _need(user, "record_payment")
    like = f"%{(q or '').strip().lower()}%"
    return [dict(r) for r in conn.execute("""
        SELECT i.id AS instalment_id, s.name AS student_name, b.code AS batch_code, i.instalment_no, i.due_date,
               i.amount, i.paid_amount, i.amount - i.paid_amount AS balance, i.status
        FROM instalments i JOIN fee_plans fp ON fp.id=i.fee_plan_id JOIN enrollments e ON e.id=fp.enrollment_id
        JOIN students s ON s.id=e.student_id JOIN batches b ON b.id=e.batch_id
        WHERE i.status != 'paid' AND (lower(s.name) LIKE ? OR lower(b.code) LIKE ?)
        ORDER BY CASE i.status WHEN 'overdue' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END, i.due_date, s.name
        LIMIT 60""", (like, like))]


# ---------------------------------------------------------------- 1. add a student and enrol them
def _seat_check(b: dict, mode: str) -> str:
    if b["status"] == "completed" or b["end_date"] < config.today().isoformat():
        raise ActionError(f"{b['code']} has already finished ({b['end_date']}). Pick a running or upcoming batch.", 409)
    if mode == "online":
        if b["mode"] == "classroom":
            raise ActionError(f"{b['code']} is classroom-only, so it can't take an online student.", 409)
        if b["online_students"] >= b["online_seat_limit"]:
            raise ActionError(f"{b['code']} is full online: {b['online_students']} of {b['online_seat_limit']} online seats "
                              f"are taken. Enrol as a classroom student, choose another batch, or raise the seat limit.", 409)
        return f"Online seat {b['online_students'] + 1} of {b['online_seat_limit']} in {b['code']}"
    if b["mode"] == "online":
        raise ActionError(f"{b['code']} is online-only, so it can't take a classroom student.", 409)
    if b["room_capacity"] and b["classroom_students"] >= b["room_capacity"]:
        raise ActionError(f"{b['code']} is full in class: {b['room_name']} seats {b['room_capacity']} and "
                          f"{b['classroom_students']} classroom students are enrolled. Enrol online instead.", 409)
    room = f" ({b['room_name']} seats {b['room_capacity']})" if b["room_capacity"] else ""
    return f"Classroom seat {b['classroom_students'] + 1}{room} in {b['code']}"


def propose_student(conn, user: dict, spec: dict) -> dict:
    _need(user, "add_student")
    name = re.sub(r"\s+", " ", str(spec.get("name") or "")).strip().title()
    if len(name) < 3 or not re.fullmatch(r"[A-Za-z .'-]+", name):
        raise ActionError("Enter the student's full name (letters only).")
    phone = normalize_phone(spec.get("phone"), "Student phone")
    parent_phone = normalize_phone(spec.get("parent_phone"), "Parent phone", required=False)
    email = (spec.get("email") or "").strip().lower() or None
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ActionError("Email doesn't look right.")
    mode = (spec.get("learning_mode") or "classroom").strip().lower()
    if mode not in ("classroom", "online"):
        raise ActionError("Learning mode must be classroom or online.")
    b = _batch(conn, user, spec.get("batch_id") or spec.get("batch_code"))
    seat = _seat_check(b, mode)
    warnings = []
    existing = conn.execute("SELECT id, name FROM students WHERE phone=?", (phone,)).fetchone()
    if existing:
        if _similar(existing["name"], name) < 0.75:
            raise ActionError(f"Phone {phone} already belongs to {existing['name']}. Check the number.", 409)
        if conn.execute("SELECT 1 FROM enrollments WHERE student_id=? AND batch_id=? AND status='active'",
                        (existing["id"], b["id"])).fetchone():
            raise ActionError(f"{existing['name']} is already enrolled in {b['code']}.", 409)
        warnings.append(f"{existing['name']} is already a student; this adds a second enrolment, no new student record.")
    joined = _date(spec.get("joined_on"), config.today(), "Joining date")
    discount = max(int(spec.get("discount") or 0), 0)
    total = int(b["total_fee"] or 0)
    if discount > total:
        raise ActionError("The discount is bigger than the course fee.")
    n = int(spec.get("instalments") or b["emi_count"] or 1)
    if not 1 <= n <= 12:
        raise ActionError("Instalments must be 1 to 12.")
    net = total - discount
    amounts = [net // n] * n
    amounts[-1] += net - sum(amounts)
    plan = [{"no": k + 1, "due_date": _add_months(joined, k).isoformat(), "amount": a} for k, a in enumerate(amounts)]
    payload = {
        "student": {"name": existing["name"] if existing else name, "phone": phone, "email": email,
                    "city": (spec.get("city") or "").strip().title() or None,
                    "parent_name": (spec.get("parent_name") or "").strip().title() or None, "parent_phone": parent_phone},
        "existing_student_id": existing["id"] if existing else None,
        "batch_id": b["id"], "batch_code": b["code"], "learning_mode": mode, "joined_on": joined.isoformat(),
        "fee": {"total": total, "discount": discount, "net": net, "plan_type": "emi" if n > 1 else "full",
                "instalments": plan},
    }
    s = payload["student"]
    payload["preview"] = _preview(
        f"New {'enrolment' if existing else 'student'}: {s['name']} in {b['code']} ({b['name']}), {mode}.",
        [("Student", s["name"]), ("Phone", phone), ("Email", email), ("City", s["city"]),
         ("Parent", f"{s['parent_name'] or '–'} {parent_phone or ''}".strip()),
         ("Batch", f"{b['code']} · {b['name']} · {b['trainer_name']}"),
         ("Mode", mode.title()), ("Joins on", joined.isoformat()),
         ("Fee", f"{_rupees(total)}" + (f" − {_rupees(discount)} discount = {_rupees(net)}" if discount else "")
          + f", {n} instalment(s)")],
        {"columns": ["EMI", "Due", "Amount"], "rows": [[p["no"], p["due_date"], _rupees(p["amount"])] for p in plan]},
        checks=[seat, "Phone number is valid and not used by another student",
                "Approved WhatsApp reminders will go straight to this number"], warnings=warnings)
    return _create(conn, user, "add_student", f"Add {s['name']} to {b['code']} ({mode})", payload)


def run_student(conn, a: dict, edits: dict | None = None) -> dict:
    p = a["payload"]
    b = _batch(conn, {"role": "owner"}, p["batch_id"])
    _seat_check(b, p["learning_mode"])                             # seats may have filled since the preview
    s = p["student"]
    sid = p.get("existing_student_id")
    if sid is None:
        if conn.execute("SELECT 1 FROM students WHERE phone=?", (s["phone"],)).fetchone():
            raise ActionError(f"Phone {s['phone']} was registered meanwhile. Make a new preview.", 409)
        sid = conn.execute("INSERT INTO students (name, phone, email, city, parent_name, guardian_phone, joined_on, "
                           "real_contact) VALUES (?,?,?,?,?,?,?,1)",       # typed in by staff: WhatsApp goes here
                           (s["name"], s["phone"], s["email"], s["city"], s["parent_name"],
                            s["parent_phone"], p["joined_on"])).lastrowid
    elif conn.execute("SELECT 1 FROM enrollments WHERE student_id=? AND batch_id=? AND status='active'",
                      (sid, b["id"])).fetchone():
        raise ActionError(f"{s['name']} is already enrolled in {b['code']}.", 409)
    eid = conn.execute("INSERT INTO enrollments (student_id, batch_id, enrolled_on, learning_mode, status) "
                       "VALUES (?,?,?,?, 'active')", (sid, b["id"], p["joined_on"], p["learning_mode"])).lastrowid
    f = p["fee"]
    fid = conn.execute("INSERT INTO fee_plans (enrollment_id, plan_type, total_amount, discount, instalments_count) "
                       "VALUES (?,?,?,?,?)", (eid, f["plan_type"], f["net"], f["discount"], len(f["instalments"]))).lastrowid
    today = config.today().isoformat()
    for i in f["instalments"]:
        conn.execute("INSERT INTO instalments (fee_plan_id, instalment_no, due_date, amount, paid_amount, status) "
                     "VALUES (?,?,?,?,0,?)", (fid, i["no"], i["due_date"], i["amount"],
                                              "due" if i["due_date"] >= today else "overdue"))
    return {"student_id": sid, "enrollment_id": eid,
            "message": f"{s['name']} added to {b['code']} with {len(f['instalments'])} instalment(s).",
            "summary": {"student_id": sid, "batch": b["code"]}}


# ---------------------------------------------------------------- 2. record a payment
def _instalment(conn, iid) -> dict:
    row = conn.execute("""
        SELECT i.*, i.amount - i.paid_amount AS balance, e.student_id, s.name AS student_name, b.code AS batch_code
        FROM instalments i JOIN fee_plans fp ON fp.id=i.fee_plan_id JOIN enrollments e ON e.id=fp.enrollment_id
        JOIN students s ON s.id=e.student_id JOIN batches b ON b.id=e.batch_id WHERE i.id=?""", (iid,)).fetchone()
    if row is None:
        raise ActionError("That instalment doesn't exist.", 404)
    return dict(row)


def find_instalment(conn, student_name: str, batch_code: str | None = None, instalment_no=None) -> int:
    """The instalment a payment is for: the student's oldest unpaid one unless a number is given."""
    rows = [dict(r) for r in conn.execute("""
        SELECT i.id, i.instalment_no, i.due_date, s.name, b.code FROM instalments i
        JOIN fee_plans fp ON fp.id=i.fee_plan_id JOIN enrollments e ON e.id=fp.enrollment_id
        JOIN students s ON s.id=e.student_id JOIN batches b ON b.id=e.batch_id
        WHERE i.status != 'paid' ORDER BY i.due_date""")]
    name = (student_name or "").strip().lower()
    hits = [r for r in rows if r["name"].lower() == name] or [r for r in rows if name and name in r["name"].lower()]
    if batch_code:
        hits = [r for r in hits if r["code"].lower() == batch_code.strip().lower()]
    if instalment_no:
        hits = [r for r in hits if r["instalment_no"] == int(instalment_no)]
    if not hits:
        raise ActionError(f"No unpaid instalment found for '{student_name}'.", 404)
    if len({r["name"] for r in hits}) > 1:
        raise ActionError("More than one student matches: " + ", ".join(sorted({r["name"] for r in hits})) +
                          ". Use the full name.", 409)
    return hits[0]["id"]


def propose_payment(conn, user: dict, spec: dict) -> dict:
    _need(user, "record_payment")
    iid = spec.get("instalment_id") or find_instalment(conn, spec.get("student_name"), spec.get("batch_code"),
                                                        spec.get("instalment_no"))
    inst = _instalment(conn, iid)
    if inst["status"] == "paid":
        raise ActionError(f"{inst['student_name']}'s EMI {inst['instalment_no']} is already paid.", 409)
    try:
        amount = int(round(float(str(spec.get("amount") or "0").replace(",", "").replace("₹", ""))))
    except ValueError:
        raise ActionError("Amount must be a number of rupees.")
    if amount <= 0:
        raise ActionError("Amount must be more than ₹0.")
    if amount > inst["balance"]:
        raise ActionError(f"{_rupees(amount)} is more than the {_rupees(inst['balance'])} still due on EMI "
                          f"{inst['instalment_no']}. Record the extra against the next EMI separately.", 409)
    method = METHODS.get(str(spec.get("method") or "UPI").strip().lower())
    if method is None:
        raise ActionError("Method must be UPI, bank transfer, cash or card.")
    ref = re.sub(r"\s+", "", str(spec.get("reference") or ""))
    if method in ("UPI", "bank_transfer") and not re.fullmatch(r"[A-Za-z0-9-]{6,30}", ref):
        raise ActionError("Enter the UPI reference / UTR from the bank (6-30 letters or digits).")
    checks, warnings = [], []
    if ref:
        dup = conn.execute("SELECT s.name, p.paid_on FROM payments p JOIN students s ON s.id=p.student_id "
                           "WHERE p.reference=?", (ref,)).fetchone()
        if dup:
            raise ActionError(f"Reference {ref} is already recorded ({dup['name']}, {dup['paid_on']}). "
                              f"The same payment can't be recorded twice.", 409)
        bank = conn.execute("SELECT txn_ref, txn_date, credit, description FROM bank_transactions WHERE utr=? "
                            "ORDER BY txn_date LIMIT 1", (ref,)).fetchone()
        if bank:
            note = f"Matches bank line {bank['txn_ref']}: {_rupees(bank['credit'])} on {bank['txn_date']} ({bank['description']})"
            (checks if bank["credit"] == amount else warnings).append(
                note if bank["credit"] == amount else note + f", but you entered {_rupees(amount)}")
        elif method in ("UPI", "bank_transfer"):
            warnings.append("No bank statement line has this reference yet; reconciliation will flag it until it arrives.")
    paid_on = _date(spec.get("paid_on"), config.today(), "Payment date")
    if paid_on > config.today():
        raise ActionError("The payment date can't be in the future.")
    after = inst["paid_amount"] + amount
    new_status = "paid" if after >= inst["amount"] else "partial"
    checks.append(f"EMI {inst['instalment_no']} goes from {inst['status']} ({_rupees(inst['balance'])} due) to "
                  f"{new_status}" + ("" if new_status == "paid" else f" ({_rupees(inst['amount'] - after)} still due)"))
    payload = {"instalment_id": inst["id"], "student_id": inst["student_id"], "student_name": inst["student_name"],
               "batch_code": inst["batch_code"], "instalment_no": inst["instalment_no"], "amount": amount,
               "method": method, "reference": ref or None, "paid_on": paid_on.isoformat(),
               "expected_paid_amount": inst["paid_amount"]}
    payload["preview"] = _preview(
        f"Record {_rupees(amount)} from {inst['student_name']} for {inst['batch_code']} EMI {inst['instalment_no']}.",
        [("Student", inst["student_name"]), ("Batch", inst["batch_code"]),
         ("EMI", f"{inst['instalment_no']} · due {inst['due_date']} · {_rupees(inst['amount'])}"),
         ("Already paid", _rupees(inst["paid_amount"])), ("Amount now", _rupees(amount)),
         ("Method", METHOD_LABEL[method]), ("Reference", ref or "–"), ("Paid on", paid_on.isoformat())],
        checks=checks, warnings=warnings)
    return _create(conn, user, "record_payment",
                   f"Record {_rupees(amount)} from {inst['student_name']} ({inst['batch_code']} EMI {inst['instalment_no']})",
                   payload)


def run_payment(conn, a: dict, edits: dict | None = None) -> dict:
    p = a["payload"]
    inst = _instalment(conn, p["instalment_id"])
    if inst["paid_amount"] != p["expected_paid_amount"] or inst["status"] == "paid":
        raise ActionError("This EMI changed after the preview was made (another payment was recorded). "
                          "Make a new preview.", 409)
    if p["reference"] and conn.execute("SELECT 1 FROM payments WHERE reference=?", (p["reference"],)).fetchone():
        raise ActionError(f"Reference {p['reference']} was recorded meanwhile.", 409)
    after = inst["paid_amount"] + p["amount"]
    status = "paid" if after >= inst["amount"] else "partial"
    conn.execute("UPDATE instalments SET paid_amount=?, status=?, paid_on=? WHERE id=?",
                 (after, status, p["paid_on"], inst["id"]))
    ref = p["reference"] or f"CASH-ACT-{a['id']}"
    pid = conn.execute("INSERT INTO payments (instalment_id, student_id, amount, paid_on, method, reference) "
                       "VALUES (?,?,?,?,?,?)", (inst["id"], inst["student_id"], p["amount"], p["paid_on"],
                                                p["method"], ref)).lastrowid
    from . import receipts
    rc = receipts.issue(conn, pid)
    return {"payment_id": pid, "instalment_status": status, "receipt_no": rc["receipt_no"], "links": rc["links"],
            "message": f"{_rupees(p['amount'])} recorded for {p['student_name']}; EMI {p['instalment_no']} is now {status}. "
                       f"Receipt {rc['receipt_no']}.",
            "summary": {"payment_id": pid, "amount": p["amount"], "receipt_no": rc["receipt_no"]}}


# ---------------------------------------------------------------- 3. attendance
def propose_attendance(conn, user: dict, spec: dict) -> dict:
    _need(user, "mark_attendance")
    b = _batch(conn, user, spec.get("batch_id") or spec.get("batch_code"))
    d = _date(spec.get("date"), config.today())
    today = config.today()
    if d > today:
        raise ActionError("Attendance can't be marked for a future date.")
    if d.isoformat() < b["start_date"] or d.isoformat() > b["end_date"]:
        raise ActionError(f"{b['code']} runs from {b['start_date']} to {b['end_date']}; {d} is outside that.")
    people = _roster(conn, b["id"])
    if not people:
        raise ActionError(f"{b['code']} has no active students.")
    by_id = {s["student_id"]: s for s in people}
    status = {s["student_id"]: "present" for s in people}
    for rec in spec.get("records") or []:                       # from the form
        sid = int(rec.get("student_id"))
        if sid not in by_id:
            raise ActionError(f"Student {sid} is not in {b['code']}.", 403)
        st = str(rec.get("status") or "present").lower()
        if st not in ("present", "late", "absent"):
            raise ActionError("Status must be present, late or absent.")
        status[sid] = st
    for key in ("absent", "late"):                              # from the copilot: names
        for s in match_names(spec.get(key) or [], people):
            status[s["student_id"]] = key
    sess = conn.execute("SELECT id FROM class_sessions WHERE batch_id=? AND session_date=? ORDER BY id LIMIT 1",
                        (b["id"], d.isoformat())).fetchone()
    warnings = []
    if b["days"] and timetable.DAY_NAMES[d.weekday()] not in b["days"]:
        warnings.append(f"{b['code']} usually meets {b['days'].replace(',', '/')}, not {timetable.DAY_NAMES[d.weekday()]}; "
                        f"this will be recorded as an extra class.")
    if sess:
        n = conn.execute("SELECT COUNT(*) FROM attendance WHERE session_id=?", (sess["id"],)).fetchone()[0]
        if n:
            warnings.append(f"Attendance for this class was already marked ({n} students); this replaces it.")
    counts = {k: sum(1 for v in status.values() if v == k) for k in ("present", "late", "absent")}
    records = [{"student_id": s["student_id"], "name": s["name"], "status": status[s["student_id"]],
                "mode": s["learning_mode"]} for s in people]
    payload = {"batch_id": b["id"], "batch_code": b["code"], "date": d.isoformat(),
               "session_id": sess["id"] if sess else None, "topic": (spec.get("topic") or "").strip() or None,
               "start_time": b["start_time"] or "09:00", "end_time": b["end_time"] or "11:00", "records": records}
    payload["preview"] = _preview(
        f"{b['code']} on {d.isoformat()}: {counts['present']} present, {counts['late']} late, {counts['absent']} absent.",
        [("Batch", f"{b['code']} · {b['name']}"), ("Date", f"{d.isoformat()} ({timetable.DAY_NAMES[d.weekday()]})"),
         ("Class", f"{payload['start_time']}–{payload['end_time']}"), ("Topic", payload["topic"] or "–")],
        {"columns": ["Student", "Joins", "Status"],
         "rows": [[r["name"], r["mode"], r["status"]] for r in sorted(records, key=lambda r: (r["status"] == "present", r["name"]))]},
        checks=[f"All {len(records)} students are enrolled in {b['code']}"], warnings=warnings)
    return _create(conn, user, "mark_attendance",
                   f"Attendance {b['code']} {d.isoformat()}: {counts['absent']} absent, {counts['late']} late", payload)


def run_attendance(conn, a: dict, edits: dict | None = None) -> dict:
    p = a["payload"]
    sid = p["session_id"]
    found = conn.execute("SELECT id FROM class_sessions WHERE batch_id=? AND session_date=? ORDER BY id LIMIT 1",
                         (p["batch_id"], p["date"])).fetchone()
    if found and found["id"] != sid:
        sid = found["id"]
    if sid is None:
        sid = conn.execute("INSERT INTO class_sessions (batch_id, session_date, start_time, end_time, topic, status) "
                           "VALUES (?,?,?,?,?, 'held')", (p["batch_id"], p["date"], p["start_time"], p["end_time"],
                                                          p["topic"])).lastrowid
    else:
        conn.execute("UPDATE class_sessions SET status='held', topic=COALESCE(?, topic) WHERE id=?", (p["topic"], sid))
        conn.execute("DELETE FROM attendance WHERE session_id=?", (sid,))
    for r in p["records"]:
        mode = None if r["status"] == "absent" else r["mode"]
        conn.execute("INSERT INTO attendance (session_id, student_id, status, mode, check_in_time, online_minutes) "
                     "VALUES (?,?,?,?,?,?)", (sid, r["student_id"], r["status"], mode,
                                             p["start_time"] if mode == "classroom" else None, None))
    absent = sum(1 for r in p["records"] if r["status"] == "absent")
    return {"session_id": sid, "message": f"Attendance saved for {p['batch_code']} on {p['date']}: "
                                          f"{len(p['records']) - absent} attended, {absent} absent.",
            "summary": {"session_id": sid, "students": len(p["records"])}}


def parent_alert(conn, user: dict, a: dict) -> dict | None:
    """After attendance is saved: a WhatsApp draft to the parents of absent students.
    It is only a preview: a counselor or the owner approves it like any other message."""
    p = a["payload"]
    absent = [r["student_id"] for r in p["records"] if r["status"] == "absent"]
    if not absent:
        return None
    rows = conn.execute(f"SELECT id, name, parent_name, guardian_phone FROM students WHERE id IN "
                        f"({','.join('?' * len(absent))}) ORDER BY name", absent).fetchall()
    course = conn.execute("SELECT c.name FROM batches b JOIN courses c ON c.id = b.course_id WHERE b.id=?",
                          (p["batch_id"],)).fetchone()[0]
    institute = db.institute_name(conn)
    when = "today" if p["date"] == config.today().isoformat() else "on " + date.fromisoformat(p["date"]).strftime("%d %b")
    items, skipped = [], []
    for r in rows:
        if not r["guardian_phone"]:
            skipped.append(r["name"])
            continue
        parent = r["parent_name"] or f"Parent of {r['name']}"
        items.append({"student_id": r["id"], "name": parent, "phone": r["guardian_phone"], "about": r["name"],
                      "message": f"Namaste {parent}, this is {institute}. {r['name']} was absent from the {course} "
                                 f"class ({p['batch_code']}) {when}. The class recording will be on the student portal. "
                                 f"Please reply here if there is anything we should know. Thank you."})
    if not items:
        return None
    payload = {"channel": "whatsapp", "audience": "parents", "template": None, "items": items,
               "skipped_no_phone": skipped, "merged_duplicates": 0, "capped_at": None, "source_action_id": a["id"]}
    nxt = _create(conn, user, "send_whatsapp",
                          f"Absence alert to parents: {p['batch_code']}, {date.fromisoformat(p['date']).strftime('%d %b')}",
                          payload)
    return {"action": nxt, "note": f"A WhatsApp alert to {len(items)} parent(s) is waiting for approval "
                                   f"(ACT-{nxt['id']}, counselor or owner)."}


# ---------------------------------------------------------------- 4. assignments and marks
def propose_assignment(conn, user: dict, spec: dict) -> dict:
    _need(user, "save_assignment")
    b = _batch(conn, user, spec.get("batch_id") or spec.get("batch_code"))
    people = _roster(conn, b["id"])
    aid = spec.get("assignment_id")
    if aid:
        row = conn.execute("SELECT id, title, due_date FROM assignments WHERE id=? AND batch_id=?",
                           (aid, b["id"])).fetchone()
        if row is None:
            raise ActionError(f"That assignment doesn't belong to {b['code']}.", 404)
        title, due = row["title"], date.fromisoformat(row["due_date"])
    else:
        title = re.sub(r"\s+", " ", str(spec.get("title") or "")).strip()
        if len(title) < 3:
            raise ActionError("Give the assignment a title.")
        due = _date(spec.get("due_date"), None, "Due date")
        if conn.execute("SELECT 1 FROM assignments WHERE batch_id=? AND lower(title)=lower(?)", (b["id"], title)).fetchone():
            raise ActionError(f"{b['code']} already has an assignment called '{title}'. Pick it from the list to enter marks.", 409)
    by_id = {s["student_id"]: s for s in people}
    scores = []
    raw = spec.get("scores") or []
    if isinstance(raw, dict):                                   # copilot: {"Student name": 78}
        names = match_names(list(raw.keys()), people)
        raw = [{"student_id": s["student_id"], "score": v} for s, v in zip(names, raw.values())]
    today = config.today()
    for r in raw:
        if r.get("score") in (None, ""):
            continue
        sid = int(r["student_id"])
        if sid not in by_id:
            raise ActionError(f"Student {sid} is not in {b['code']}.", 403)
        try:
            score = int(round(float(r["score"])))
        except (TypeError, ValueError):
            raise ActionError(f"Score for {by_id[sid]['name']} must be a number.")
        if not 0 <= score <= 100:
            raise ActionError(f"Score for {by_id[sid]['name']} must be 0 to 100.")
        st = (r.get("status") or ("late" if today > due else "submitted")).lower()
        scores.append({"student_id": sid, "name": by_id[sid]["name"], "score": score,
                       "status": st if st in ("submitted", "late") else "submitted"})
    if not aid and not title:
        raise ActionError("Give the assignment a title.")
    if aid and not scores:
        raise ActionError("Enter at least one score.")
    missing = [s["name"] for s in people if s["student_id"] not in {x["student_id"] for x in scores}]
    payload = {"batch_id": b["id"], "batch_code": b["code"], "assignment_id": aid, "title": title,
               "due_date": due.isoformat(), "scores": scores}
    summary = (f"{'New assignment' if not aid else 'Marks for'} '{title}' in {b['code']}"
               + (f", {len(scores)} score(s)" if scores else ", no marks yet") + ".")
    payload["preview"] = _preview(
        summary,
        [("Batch", f"{b['code']} · {b['name']}"), ("Assignment", title), ("Due", due.isoformat()),
         ("Marks entered", f"{len(scores)} of {len(people)}")],
        {"columns": ["Student", "Score", "Status"], "rows": [[s["name"], s["score"], s["status"]] for s in scores]}
        if scores else None,
        checks=["Scores are 0 to 100 and every student is in this batch"] if scores else [f"Students in {b['code']}: {len(people)}"],
        warnings=[f"No score yet for: {', '.join(missing[:8])}{'…' if len(missing) > 8 else ''} (counts as not submitted)"]
        if scores and missing else [])
    return _create(conn, user, "save_assignment", summary.rstrip("."), payload)


def run_assignment(conn, a: dict, edits: dict | None = None) -> dict:
    p = a["payload"]
    aid = p["assignment_id"]
    if not aid:
        if conn.execute("SELECT 1 FROM assignments WHERE batch_id=? AND lower(title)=lower(?)",
                        (p["batch_id"], p["title"])).fetchone():
            raise ActionError(f"An assignment called '{p['title']}' was added meanwhile.", 409)
        aid = conn.execute("INSERT INTO assignments (batch_id, title, due_date) VALUES (?,?,?)",
                           (p["batch_id"], p["title"], p["due_date"])).lastrowid
    today = config.today().isoformat()
    for s in p["scores"]:
        hit = conn.execute("SELECT id FROM submissions WHERE assignment_id=? AND student_id=?",
                           (aid, s["student_id"])).fetchone()
        if hit:
            conn.execute("UPDATE submissions SET score=?, status=? WHERE id=?", (s["score"], s["status"], hit["id"]))
        else:
            conn.execute("INSERT INTO submissions (assignment_id, student_id, submitted_on, score, status) "
                         "VALUES (?,?,?,?,?)", (aid, s["student_id"], today, s["score"], s["status"]))
    return {"assignment_id": aid, "message": f"Saved '{p['title']}' for {p['batch_code']}"
                                             + (f" with {len(p['scores'])} score(s)." if p["scores"] else "."),
            "summary": {"assignment_id": aid, "scores": len(p["scores"])}}


RUNNERS = {"add_student": run_student, "record_payment": run_payment,
           "mark_attendance": run_attendance, "save_assignment": run_assignment}


def check_scope(conn, user: dict, a: dict) -> None:
    """When confirming: a trainer may only confirm changes to their own batches."""
    if user["role"] == "trainer" and a["action_type"] in ("mark_attendance", "save_assignment"):
        _batch(conn, user, a["payload"]["batch_id"])
