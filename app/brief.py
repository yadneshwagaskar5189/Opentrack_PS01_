"""The morning brief: one screen (and one WhatsApp-ready text) with what needs attention today.

Plain SQL, no AI, so it is instant and always right. Each role only gets what it may see:
trainers never see fees, accounts staff never see attendance.
"""
from datetime import timedelta

from . import actions, config, db, planner, reconcile, risk


def _hm(t: str) -> str:
    return t[:5] if t else ""


def _money(n) -> str:
    n = int(n or 0)
    s = str(n)
    if len(s) > 3:                                    # Indian grouping: 1,23,456
        head, tail = s[:-3], s[-3:]
        head = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1])
        s = f"{head},{tail}"
    return f"₹{s}"


def _day(iso: str) -> str:
    from datetime import date
    return date.fromisoformat(iso).strftime("%a %d %b")


def _lectures(conn, day: str, trainer_id: int | None = None) -> list[dict]:
    sql = ("SELECT start_time, end_time, batch_code, course_name, trainer_name, room_name, trainer_on_leave, status "
           "FROM v_schedule WHERE class_date = ?")
    args: list = [day]
    if trainer_id:
        sql += " AND trainer_id = ?"
        args.append(trainer_id)
    return [dict(r) for r in conn.execute(sql + " ORDER BY start_time", args)]


def _lecture_line(c: dict, with_trainer: bool = True) -> str:
    who = f" · {c['trainer_name']}" if with_trainer else ""
    leave = " · trainer ON LEAVE" if c["trainer_on_leave"] else ""
    return f"{_hm(c['start_time'])}–{_hm(c['end_time'])} {c['batch_code']} {c['course_name']}{who} · {c['room_name']}{leave}"


def _fees(conn, today: str, week_end: str) -> dict:
    def q(where, args):
        r = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(amount - paid_amount), 0) FROM instalments WHERE {where}", args).fetchone()
        return {"count": r[0], "amount": r[1]}
    return {"today": q("status != 'paid' AND due_date = ?", (today,)),
            "week": q("status != 'paid' AND due_date BETWEEN ? AND ?", (today, week_end)),
            "overdue": q("status IN ('overdue', 'partial') AND due_date < ?", (today,))}


def _pending(conn, user) -> int:
    return len(actions.list_actions(conn, user, "pending"))


def build(conn, user: dict) -> dict:
    role = user["role"]
    t = config.today()
    today, tomorrow, yesterday = t.isoformat(), (t + timedelta(days=1)).isoformat(), (t - timedelta(days=1)).isoformat()
    week_end = (t + timedelta(days=6)).isoformat()
    sections: list[dict] = []

    def add(key, title, lines, tone="info", ask=None, empty=None):
        if lines or empty:
            sections.append({"key": key, "title": title, "lines": lines or [empty], "tone": tone if lines else "ok", "ask": ask})

    if role in ("owner", "counselor", "trainer"):
        tid = user.get("trainer_id") if role == "trainer" else None
        lec = _lectures(conn, today, tid)
        add("today", "Your lectures today" if tid else "Today's lectures",
            [_lecture_line(c, not tid) for c in lec], "warn" if any(c["trainer_on_leave"] for c in lec) else "info",
            "What are today's lectures?", "No lectures today.")
        if tid:
            nxt = _lectures(conn, tomorrow, tid)
            add("tomorrow", "Tomorrow", [_lecture_line(c, False) for c in nxt], "info", "What is tomorrow's timetable?",
                "No lectures tomorrow.")
            missing = conn.execute("""
                SELECT b.code, cs.session_date FROM class_sessions cs JOIN batches b ON b.id = cs.batch_id
                WHERE b.trainer_id = ? AND cs.status = 'held' AND cs.session_date < ?
                  AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.session_id = cs.id) ORDER BY cs.session_date""",
                                   (tid, today)).fetchall()
            add("recordings", "Recordings to upload", [f"{m[0]} class of {_day(m[1])}" for m in missing], "warn",
                "Which classes are missing a recording?")
        else:
            subs = conn.execute("""
                SELECT class_date, day_name, start_time, batch_code, trainer_name FROM v_schedule
                WHERE trainer_on_leave = 1 AND class_date BETWEEN ? AND ? ORDER BY class_date, start_time""",
                                (today, week_end)).fetchall()
            add("leave", "Substitute needed", [f"{_day(s[0])} {_hm(s[2])} {s[3]}: {s[4]} is on leave" for s in subs],
                "warn", "Which classes are affected by trainer leave?")
        try:
            rk = [r for r in risk.at_risk(conn, user)["students"] if r["level"] == "high"]
        except risk.RiskError:
            rk = []
        add("risk", "Students at high risk", [f"{r['name']} ({r['batch_code']}): {', '.join(r['reasons'][:2])}" for r in rk[:4]]
            + ([f"…and {len(rk) - 4} more"] if len(rk) > 4 else []), "warn", "Which students have attendance below 75%?")
        att = conn.execute("""
            SELECT b.code, SUM(a.status != 'absent'), COUNT(*) FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
            JOIN batches b ON b.id = cs.batch_id WHERE cs.session_date = ?""" + (" AND b.trainer_id = ?" if tid else "") +
                           " GROUP BY b.code ORDER BY b.code", (yesterday, tid) if tid else (yesterday,)).fetchall()
        add("attendance", "Yesterday's attendance", [f"{a[0]}: {a[1]} of {a[2]} present ({round(100 * a[1] / a[2])}%)"
                                                     for a in att if a[2]], "info", "What was yesterday's attendance?")

    if role in ("owner", "counselor", "accounts"):
        f = _fees(conn, today, week_end)
        lines = []
        if f["today"]["count"]:
            lines.append(f"Due today: {f['today']['count']} EMI(s), {_money(f['today']['amount'])}")
        lines.append(f"Due this week: {f['week']['count']} EMI(s), {_money(f['week']['amount'])}")
        if f["overdue"]["count"]:
            lines.append(f"Overdue: {f['overdue']['count']} EMI(s), {_money(f['overdue']['amount'])}")
        add("fees", "Fees", lines, "warn" if f["overdue"]["count"] else "info", "Which EMIs are due this week?")

    if role in ("owner", "accounts"):
        y = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payments WHERE paid_on = ?", (yesterday,)).fetchone()
        m = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE paid_on BETWEEN ? AND ?",
                         (t.replace(day=1).isoformat(), today)).fetchone()
        lines = [f"Yesterday: {y[0]} payment(s), {_money(y[1])}", f"This month so far: {_money(m[0])}"]
        try:
            rec = reconcile.reconcile(conn)
            todo = int(rec.get("needs_attention") or 0)
            if todo:
                lines.append(f"Bank reconciliation: {todo} line(s) need a look")
        except Exception:                              # no bank statement loaded
            pass
        add("collections", "Collections", lines, "info", "How much fee did we collect this month?")

    waiting = []
    n = _pending(conn, user)
    if n:
        waiting.append(f"{n} proposal(s) waiting in Approvals")
    if role == "owner":
        problems = len(planner.current(conn, user)["problems"])
        if problems:
            waiting.append(f"{problems} timetable problem(s) in the planning week: open Timetable → Fix clashes")
    if role in ("owner", "counselor"):
        ready = conn.execute("SELECT COUNT(*) FROM v_certificate_eligibility WHERE eligible = 1 AND certificate_no IS NULL "
                             "AND batch_status = 'completed'").fetchone()[0]
        if ready:
            waiting.append(f"{ready} certificate(s) ready to issue")
    add("waiting", "Waiting for you", waiting, "warn", None, "Nothing waiting for you.")

    first = user.get("display_name", "").split(" ")[0]
    institute = db.institute_name(conn)
    headline = f"Good morning, {first}. Here is {institute} on {t.strftime('%A %d %b')}."
    text = "\n\n".join([f"*{institute}: morning brief, {t.strftime('%a %d %b')}*"] +
                       [f"*{s['title']}*\n" + "\n".join(f"• {line}" for line in s["lines"]) for s in sections])
    return {"date": today, "headline": headline, "sections": sections, "text": text}
