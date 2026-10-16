"""Offline stand-in for the LLM (LLM_PROVIDER=mock).

It understands a handful of demo questions with simple keyword rules, so your
frontend teammate can build against the API without an API key, and you have
a fallback if the internet dies during the demo. It deliberately does NOT
know about roles: role security is enforced by the database guard, so asking
a fee question as a trainer still shows the guard blocking it.
"""
import re

from . import config, question_bank


def _week():
    ws, we = config.due_window()
    return ws.isoformat(), we.isoformat()


def plan(question: str, user: dict, context: dict) -> dict:
    q = question.lower()
    trainer = user["role"] == "trainer"
    ws, we = _week()
    today = config.today().isoformat()
    java = "java" in q
    institute = context.get("institute") or "the institute"
    codes = [b["code"] for b in context.get("batches", []) if re.search(rf"\b{re.escape(b['code'].lower())}\b", q)]
    if codes:
        course_filter = "AND e.batch_code IN (" + ", ".join(f"'{c}'" for c in codes) + ")"
    else:
        course_filter = "AND e.course_name LIKE '%Java%'" if java else ""

    if re.search(r"\b(miss|missed|absent|absence)", q) and re.search(r"emi|instal|fee|due", q):
        n = int(m.group(1)) if (m := re.search(r"(\d+)\s*(or more|\+)?\s*class", q)) else 3
        sql = f"""SELECT s.id AS student_id, s.name, s.phone, e.batch_code,
  COUNT(DISTINCT a.id) AS classes_missed, i.due_date, i.balance AS amount_due
FROM v_students s
JOIN v_enrollments e ON e.student_id = s.id AND e.status = 'active' {course_filter}
JOIN v_attendance a ON a.student_id = s.id AND a.batch_id = e.batch_id AND a.status = 'absent'
JOIN v_instalments i ON i.student_id = s.id AND i.batch_id = e.batch_id
  AND i.status != 'paid' AND i.due_date BETWEEN '{ws}' AND '{we}'
GROUP BY s.id, i.id
HAVING COUNT(DISTINCT a.id) >= {n}
ORDER BY classes_missed DESC"""
        if re.search(r"draft|remind|whatsapp|message|send", q):
            return {"type": "action", "action_type": "send_whatsapp", "sql": sql,
                    "title": "EMI + attendance reminder",
                    "message_template": ("Hi {name}, this is " + institute + ". We noticed you've missed "
                                         "{classes_missed} classes in {batch_code}, and your EMI of ₹{amount_due} "
                                         "is due on {due_date}. Recordings are on the portal; do reach out if "
                                         "you need help catching up or with the payment. Thank you!"),
                    "explanation": f"Students who missed {n}+ classes and have an unpaid EMI due this week."}
        return {"type": "query", "sql": sql,
                "explanation": f"Students who missed {n}+ classes and have an unpaid EMI due this week."}

    entry_plan = _entry_plan(question, q, codes, today)
    if entry_plan:
        return entry_plan

    if re.search(r"certificate", q):
        where = f"WHERE batch_code IN ({', '.join(repr(c) for c in codes)})" if codes else "WHERE batch_status = 'completed'"
        return {"type": "query", "explanation": "Certificate check: attendance of at least 75% and fees fully paid.",
                "sql": "SELECT student_name, batch_code, attendance_pct, fee_balance, eligible, reason "
                       f"FROM v_certificate_eligibility {where} ORDER BY eligible DESC, student_name"}

    if "recording" in q and re.search(r"missing|without|no recording|not uploaded|not recorded", q):
        sess, rec = ("t_my_sessions", "t_my_recordings") if trainer else ("v_class_sessions", "v_recordings")
        if trainer:
            sql = (f"SELECT s.batch_code, s.session_date, s.topic FROM {sess} s WHERE NOT EXISTS "
                   f"(SELECT 1 FROM {rec} r WHERE r.batch_code = s.batch_code AND r.session_date = s.session_date) "
                   f"AND s.session_date < '{today}' ORDER BY s.batch_code, s.session_date")
        else:
            sql = (f"SELECT s.batch_code, s.session_date, s.topic FROM {sess} s LEFT JOIN {rec} r ON r.session_id = s.id "
                   f"WHERE r.id IS NULL AND s.session_date < '{today}' ORDER BY s.batch_code, s.session_date")
        return {"type": "query", "explanation": "Classes already held that have no recording uploaded.", "sql": sql}

    if re.search(r"bank|reconcil|utr|unmatched", q):
        return {"type": "query", "explanation": "Bank credits with no matching payment reference.",
                "sql": "SELECT b.txn_date, b.description, b.credit, b.utr FROM v_bank_statement b "
                       "LEFT JOIN v_payments p ON p.reference = b.utr WHERE p.id IS NULL ORDER BY b.txn_date"}

    if re.search(r"seat", q) or (re.search(r"online", q) and re.search(r"one more|another|room|space|full|9th|add", q)):
        view = "t_my_batches" if trainer else "v_batches"
        where = f"WHERE code IN ({', '.join(repr(c) for c in codes)})" if codes else "WHERE status = 'active'"
        return {"type": "query", "explanation": "Online seats used and left per batch.",
                "sql": f"SELECT code, online_seat_limit, online_students, online_seat_limit - online_students AS online_seats_left "
                       f"FROM {view} {where} ORDER BY online_seats_left"}

    if re.search(r"double|clash|conflict|overlap", q):
        if trainer:
            return {"type": "query", "explanation": "Overlapping classes in your timetable.",
                    "sql": "SELECT a.day_name, a.start_hour, a.batch_code AS class_1, b.batch_code AS class_2 "
                           "FROM t_my_timetable a JOIN t_my_timetable b ON a.day_of_week=b.day_of_week "
                           "AND a.id<b.id AND a.start_hour<b.end_hour AND b.start_hour<a.end_hour"}
        return {"type": "query", "explanation": "Trainers booked for two classes at the same time this week.",
                "sql": "SELECT a.trainer_name, a.day_name, a.batch_code AS class_1, a.start_hour AS class_1_start, a.end_hour AS class_1_end, "
                       "b.batch_code AS class_2, b.start_hour AS class_2_start, b.end_hour AS class_2_end FROM v_timetable a JOIN v_timetable b ON a.trainer_id=b.trainer_id "
                       "AND a.day_of_week=b.day_of_week AND a.id<b.id AND a.start_hour<b.end_hour "
                       "AND b.start_hour<a.end_hour ORDER BY a.day_of_week"}

    if re.search(r"(unavailable|sick|leave|absent today|not available).*(re-?plan|timetable|schedule)|re-?plan", q):
        names = context.get("trainers", [])
        if not names:
            return {"type": "refuse", "reason": "Only the owner can re-plan the timetable."}
        hit = next((t for t in names if t["name"].split()[0].lower() in q), None)
        days = [d for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday") if d in q or d[:3] + " " in q + " "]
        if hit and days:
            return {"type": "action", "action_type": "replan_timetable", "trainer_id": hit["id"],
                    "days": [d[:3].title() for d in days], "reason": "sick leave" if "sick" in q else "unavailable",
                    "explanation": f"Re-plan {hit['name']}'s classes."}

    if "mark" in q and "paid" in q:
        m = re.search(r"mark\s+(.+?)(?:'s|’s)?\s+(?:emi|instal|fee)", q)
        name = m.group(1).strip() if m else ""
        safe = name.replace("'", "''")
        return {"type": "action", "action_type": "mark_instalment_paid", "title": f"Mark {name.title()}'s EMI paid",
                "sql": f"SELECT id AS instalment_id, student_name, due_date, balance FROM v_instalments "
                       f"WHERE lower(student_name) LIKE '%{safe}%' AND status != 'paid' ORDER BY due_date LIMIT 1",
                "explanation": "The next unpaid instalment for that student."}

    # the 30 checked everyday questions (app/question_bank.py): fee structure, today's lectures, which trainer...
    entry = question_bank.match(question, user["role"])
    if entry:
        return question_bank.plan_for(entry, user["role"], institute)

    if re.search(r"overdue|pending fee|defaulter", q):
        return {"type": "query", "explanation": "Overdue EMIs, biggest first.",
                "sql": "SELECT student_name, batch_code, instalment_no, due_date, balance FROM v_instalments "
                       "WHERE status IN ('overdue','partial') AND due_date < '" + today + "' ORDER BY balance DESC"}

    if re.search(r"fee|payment|paid|emi|instal|revenue|collect", q):
        return {"type": "query", "explanation": "Fee instalments due this week.",
                "sql": f"SELECT student_name, batch_code, due_date, balance FROM v_instalments WHERE status != 'paid' "
                       f"AND due_date BETWEEN '{ws}' AND '{we}' ORDER BY due_date"}

    if re.search(r"missed the most|most absen|absent the most|missed most", q):
        view = "t_my_attendance" if trainer else "v_attendance"
        return {"type": "query", "explanation": "Students with the most missed classes.",
                "sql": f"SELECT student_name, batch_code, SUM(status = 'absent') AS classes_missed, COUNT(*) AS classes_held "
                       f"FROM {view} GROUP BY student_id, batch_code HAVING classes_missed > 0 "
                       f"ORDER BY classes_missed DESC LIMIT 10"}

    if re.search(r"attendance", q):
        view = "t_my_attendance" if trainer else "v_attendance"
        return {"type": "query", "explanation": "Attendance rate per batch.",
                "sql": f"SELECT batch_code, ROUND(100.0 * SUM(status != 'absent') / COUNT(*), 1) AS attendance_pct, "
                       f"COUNT(DISTINCT student_id) AS students FROM {view} GROUP BY batch_code ORDER BY attendance_pct"}

    if re.search(r"risk|falling behind|struggl|weak", q):
        view = "t_my_attendance" if trainer else "v_attendance"
        return {"type": "query", "explanation": "Students with the most absences in the last 3 weeks.",
                "sql": f"SELECT student_name, batch_code, SUM(status='absent') AS absences_last_3_weeks FROM {view} "
                       f"WHERE session_date >= date('{today}', '-21 days') GROUP BY student_id, batch_code "
                       f"HAVING absences_last_3_weeks >= 2 ORDER BY absences_last_3_weeks DESC"}

    if re.search(r"how many students|number of students|student count", q):
        if trainer:
            return {"type": "query", "explanation": "Your students per batch.",
                    "sql": "SELECT batch_code, COUNT(*) AS students FROM t_my_students "
                           "WHERE enrollment_status='active' GROUP BY batch_code"}
        return {"type": "query", "explanation": "Active students per batch.",
                "sql": "SELECT batch_code, COUNT(*) AS students FROM v_enrollments WHERE status='active' "
                       "GROUP BY batch_code ORDER BY students DESC"}

    if "recording" in q:
        view = "t_my_recordings" if trainer else "v_recordings"
        return {"type": "query", "explanation": "Most-watched recordings.",
                "sql": f"SELECT batch_code, session_date, topic, views FROM {view} ORDER BY views DESC LIMIT 10"}

    if re.search(r"timetable|schedule|classes (today|this week)", q):
        view = "t_my_timetable" if trainer else "v_timetable"
        return {"type": "query", "explanation": "This week's timetable.",
                "sql": f"SELECT day_name, start_hour, end_hour, batch_code, room_name FROM {view} "
                       f"ORDER BY day_of_week, start_hour"}

    return {"type": "refuse",
            "reason": "Offline demo mode only understands the sample questions (see the question list: fees, "
                      "today's lectures, trainers, attendance, reminders...). Add an LLM API key to ask anything."}


PHONE = r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}"
NAME = r"[A-Za-z][A-Za-z.' ]*?[A-Za-z]"          # lazy: stops at the first word that ends the name


def _names(text: str) -> list[str]:
    parts = re.split(r",|\band\b|&", text or "")
    return [p.strip(" .:;") for p in parts if re.search(r"[A-Za-z]{2}", p or "")]


def _entry_plan(question: str, q: str, codes: list[str], today: str) -> dict | None:
    """Requests to add data: new student, payment, attendance, assignment."""
    named = re.findall(r"\b([A-Za-z]{1,3}\d{1,3})\b", question)        # e.g. B03 even if it isn't your batch
    batch = codes[0] if codes else (named[0].upper() if named else None)
    # 1. "Add Riya Shah, 9876543210, parent Sunil Shah 9822012345, to B01 online"
    if re.match(r"\s*(add|enrol|enroll|register|admit)\b", q) and re.search(PHONE, question) and "assignment" not in q:
        m = re.match(rf"\s*(?:add|enrol|enroll|register|admit)\s+(?:a\s+)?(?:new\s+)?(?:student\s+)?(?:named\s+)?({NAME})\s*(?:,|\(|\+|\d)",
                     question, re.I)
        phones = re.findall(PHONE, question)
        parent = re.search(rf"(?:parent|father|mother|guardian)\s*(?:name)?\s*(?:is|:)?\s*({NAME})\s*[,(]?\s*(?:{PHONE}|,|$|\bto\b)",
                           question, re.I)
        plan = {"type": "action", "action_type": "add_student", "name": m.group(1).strip() if m else "",
                "phone": phones[0], "batch_code": batch,
                "learning_mode": "online" if re.search(r"\bonline\b", q) else "classroom",
                "explanation": "A new student, enrolled after you confirm."}
        if parent:
            plan["parent_name"] = parent.group(1).strip()
        if len(phones) > 1:
            plan["parent_phone"] = phones[1]
        return plan
    # 2. "Record a payment of ₹11,000 from Rohan Gupta by UPI, ref 412398765012"
    if re.search(r"\b(record|log|enter|add)\b.*\bpayment\b|\breceived\b.*\bfrom\b", q):
        amt = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+)|payment of\s*([\d,]+)|received\s*([\d,]+)", q)
        who = re.search(rf"\bfrom\s+({NAME})(?:'s)?\s*(?:,|\bby\b|\bvia\b|\bthrough\b|\busing\b|\bin\b|\bon\b|\bfor\b|\bref|\butr|$)",
                        question, re.I)
        ref = re.search(r"(?:ref(?:erence)?|utr|txn)\s*(?:no\.?|number|id)?\s*[:#]?\s*([A-Za-z0-9-]{4,})", question, re.I)
        method = ("cash" if "cash" in q else "card" if re.search(r"\bcard\b", q) else
                  "bank_transfer" if re.search(r"neft|imps|rtgs|bank transfer", q) else "UPI")
        plan = {"type": "action", "action_type": "record_payment", "student_name": who.group(1).strip() if who else "",
                "amount": int((next(g for g in amt.groups() if g) if amt else "0").replace(",", "")),
                "method": method, "explanation": "A payment against the student's oldest unpaid EMI."}
        if ref:
            plan["reference"] = ref.group(1)
        if batch:
            plan["batch_code"] = batch
        return plan
    # 3. "Mark attendance for B01 today: everyone present except Aarav Sharma"
    if re.search(r"\b(mark|take|record|enter)\b.*\battendance\b", q):
        date = re.search(r"\d{4}-\d{2}-\d{2}", q)
        absent = re.search(r"(?:except|absent\s*[:\-]?|apart from|but not)\s+(.+?)(?:\.\s|;|\blate\b|$)", question, re.I)
        late = re.search(r"late\s*[:\-]?\s*(.+?)(?:\.\s|;|$)", question, re.I)
        return {"type": "action", "action_type": "mark_attendance", "batch_code": batch,
                "date": date.group(0) if date else ("yesterday" if "yesterday" in q else today),
                "absent": [n for n in _names(absent.group(1) if absent else "") if not re.search(r"\blate\b", n, re.I)],
                "late": _names(late.group(1)) if late else [],
                "explanation": "Everyone not named is marked present."}
    # 4. "Add assignment 'Mini project' for B01 due 2026-10-25" (+ optional "Aarav 78, Sneha 91")
    if re.search(r"\b(add|create|set|new)\b.*\bassignment\b|\b(enter|add|record)\b.*\b(marks|scores)\b", q):
        title = re.search(r"['\"“‘](.+?)['\"”’]", question)
        due = re.search(r"due\s*(?:on|by)?\s*(\d{4}-\d{2}-\d{2})", q)
        scores = dict((n.strip(), int(v)) for n, v in re.findall(rf"({NAME})\s*[:=-]?\s*(\d{{1,3}})\b", question.split(":", 1)[-1])
                      if not re.search(r"\b(due|b\d+|assignment|for)\b", n, re.I))
        plan = {"type": "action", "action_type": "save_assignment", "batch_code": batch,
                "title": title.group(1) if title else "", "explanation": "An assignment saved after you confirm."}
        if due:
            plan["due_date"] = due.group(1)
        if scores:
            plan["scores"] = scores
        return plan
    return None


def summarize(question: str, plan: dict, result: dict) -> str:
    n = result["row_count"]
    if n == 0:
        return "No matching records found."
    rows = result["rows"]
    first_col = result["columns"][0]
    if n == 1 and len(result["columns"]) <= 2:
        return f"{plan.get('explanation', 'Result')}: " + ", ".join(f"{k} = {v}" for k, v in rows[0].items())
    names = ", ".join(str(r[first_col]) for r in rows[:5])
    more = f" and {n - 5} more" if n > 5 else ""
    return f"{plan.get('explanation', 'Found results.')} {n} row(s): {names}{more}."
