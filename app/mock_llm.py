"""Offline stand-in for the LLM (LLM_PROVIDER=mock).

It understands a handful of demo questions with simple keyword rules, so your
frontend teammate can build against the API without an API key, and you have
a fallback if the internet dies during the demo. It deliberately does NOT
know about roles: role security is enforced by the database guard, so asking
a fee question as a trainer still shows the guard blocking it.
"""
import re

from . import config


def _week():
    ws, we = config.week_bounds()
    return ws.isoformat(), we.isoformat()


def plan(question: str, user: dict, context: dict) -> dict:
    q = question.lower()
    trainer = user["role"] == "trainer"
    ws, we = _week()
    today = config.today().isoformat()
    java = "java" in q
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
                    "message_template": ("Hi {name}, this is TechSummit Trainers. We noticed you've missed "
                                         "{classes_missed} classes in {batch_code}, and your EMI of ₹{amount_due} "
                                         "is due on {due_date}. Recordings are on the portal; do reach out if "
                                         "you need help catching up or with the payment. Thank you!"),
                    "explanation": f"Students who missed {n}+ classes and have an unpaid EMI due this week."}
        return {"type": "query", "sql": sql,
                "explanation": f"Students who missed {n}+ classes and have an unpaid EMI due this week."}

    if re.search(r"double|clash|conflict|overlap", q):
        if trainer:
            return {"type": "query", "explanation": "Overlapping classes in your timetable.",
                    "sql": "SELECT a.day_name, a.start_hour, a.batch_code AS class_1, b.batch_code AS class_2 "
                           "FROM t_my_timetable a JOIN t_my_timetable b ON a.day_of_week=b.day_of_week "
                           "AND a.id<b.id AND a.start_hour<b.end_hour AND b.start_hour<a.end_hour"}
        return {"type": "query", "explanation": "Trainers booked for two classes at the same time this week.",
                "sql": "SELECT a.trainer_name, a.day_name, a.start_hour || ':00' AS starts, a.batch_code AS class_1, "
                       "b.batch_code AS class_2 FROM v_timetable a JOIN v_timetable b ON a.trainer_id=b.trainer_id "
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

    if re.search(r"overdue|pending fee|defaulter", q):
        return {"type": "query", "explanation": "Overdue EMIs, biggest first.",
                "sql": "SELECT student_name, batch_code, instalment_no, due_date, balance FROM v_instalments "
                       "WHERE status IN ('overdue','partial') AND due_date < '" + today + "' ORDER BY balance DESC"}

    if re.search(r"fee|payment|paid|emi|instal|revenue|collect", q):
        return {"type": "query", "explanation": "Fee instalments due this week.",
                "sql": f"SELECT student_name, batch_code, due_date, balance FROM v_instalments WHERE status != 'paid' "
                       f"AND due_date BETWEEN '{ws}' AND '{we}' ORDER BY due_date"}

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
            "reason": "Offline demo mode only understands a few sample questions (absences + EMIs, clashes, "
                      "overdue fees, attendance, at-risk students, recordings, timetable). Add an LLM API key "
                      "to ask anything."}


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
