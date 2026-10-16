"""Role-aware dashboard numbers and simple lists for the frontend.

Lists go through sql_guard with fixed SQL, so they obey exactly the same
role rules as the copilot."""
from datetime import timedelta

from . import config, planner, risk, sql_guard


def _one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def summary(conn, user: dict) -> dict:
    role = user["role"]
    today = config.today()
    ws, we = config.week_bounds(today)
    month_start = today.replace(day=1).isoformat()
    out: dict = {"role": role, "today": today.isoformat(), "week_start": ws.isoformat(), "week_end": we.isoformat()}

    def fees():
        due = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(amount-paid_amount),0) amt FROM instalments "
                         "WHERE status!='paid' AND due_date BETWEEN ? AND ?", (ws.isoformat(), we.isoformat()))
        over = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(amount-paid_amount),0) amt FROM instalments "
                          "WHERE status IN ('overdue','partial') AND due_date < ?", (today.isoformat(),))
        coll = _one(conn, "SELECT COALESCE(SUM(amount),0) amt FROM payments WHERE paid_on >= ?", (month_start,))
        return {"due_this_week": {"count": due["n"], "amount": due["amt"]},
                "overdue": {"count": over["n"], "amount": over["amt"]},
                "collected_this_month": coll["amt"]}

    def pending():
        if role == "owner":
            return _one(conn, "SELECT COUNT(*) FROM pending_actions WHERE status='pending'")[0]
        return _one(conn, "SELECT COUNT(*) FROM pending_actions WHERE status='pending' AND created_by=?",
                    (user["id"],))[0]

    def classes_today(trainer_id=None):
        sql = ("SELECT s.start_hour, s.end_hour, b.code AS batch_code, t.name AS trainer_name, r.name AS room_name, "
               "s.is_online FROM timetable_slots s JOIN batches b ON b.id=s.batch_id JOIN trainers t ON t.id=s.trainer_id "
               "LEFT JOIN rooms r ON r.id=s.room_id WHERE s.week_start=? AND s.day_of_week=?")
        args = [ws.isoformat(), today.weekday()]
        if trainer_id:
            sql += " AND s.trainer_id=?"
            args.append(trainer_id)
        return [dict(r) for r in conn.execute(sql + " ORDER BY s.start_hour", args)]

    if role in ("owner", "counselor"):
        out["active_students"] = _one(conn, "SELECT COUNT(DISTINCT student_id) FROM enrollments WHERE status='active'")[0]
        out["active_batches"] = _one(conn, "SELECT COUNT(*) FROM batches WHERE status='active'")[0]
        out["classes_today"] = classes_today()
        out["timetable_problems"] = len(planner.current(conn, user)["problems"])
        out["at_risk"] = _risk_counts(conn, user)
        out["fees"] = fees()
    elif role == "accounts":
        out["fees"] = fees()
        out["collections_by_method"] = [dict(r) for r in conn.execute(
            "SELECT method, COUNT(*) AS payments, SUM(amount) AS amount FROM payments WHERE paid_on >= ? "
            "GROUP BY method ORDER BY amount DESC", (month_start,))]
    else:  # trainer
        tid = user["trainer_id"]
        out["my_batches"] = [dict(r) for r in conn.execute(
            "SELECT b.code, c.name AS course, b.mode, (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id "
            "AND e.status='active') AS students FROM batches b JOIN courses c ON c.id=b.course_id "
            "WHERE b.trainer_id=? AND b.status='active'", (tid,))]
        out["classes_today"] = classes_today(tid)
        att = _one(conn, "SELECT COUNT(*) n, SUM(a.status!='absent') present FROM attendance a "
                         "JOIN class_sessions cs ON cs.id=a.session_id JOIN batches b ON b.id=cs.batch_id "
                         "WHERE b.trainer_id=? AND cs.session_date >= ?",
                   (tid, (today - timedelta(days=14)).isoformat()))
        out["attendance_last_14_days_pct"] = round(100 * att["present"] / att["n"], 1) if att["n"] else None
        out["at_risk"] = _risk_counts(conn, user)
    out["pending_actions"] = pending()
    return out


def _risk_counts(conn, user) -> dict:
    rows = risk.at_risk(conn, user)["students"]
    return {"high": sum(r["level"] == "high" for r in rows), "medium": sum(r["level"] == "medium" for r in rows)}


LIST_SQL = {
    "students": {
        "trainer": "SELECT student_id AS id, name, email, batch_code, learning_mode, enrollment_status "
                   "FROM t_my_students ORDER BY name",
        "default": "SELECT s.id, s.name, s.phone, s.email, s.city, group_concat(e.batch_code, ', ') AS batches "
                   "FROM v_students s LEFT JOIN v_enrollments e ON e.student_id = s.id AND e.status = 'active' "
                   "GROUP BY s.id ORDER BY s.name",
    },
    "batches": {
        "trainer": "SELECT * FROM t_my_batches ORDER BY code",
        "default": "SELECT * FROM v_batches ORDER BY code",
    },
    "trainers": {
        "default": "SELECT * FROM v_trainers_public ORDER BY name",
    },
    "rooms": {
        "default": "SELECT * FROM v_rooms ORDER BY capacity DESC",
    },
}


def list_items(user: dict, kind: str) -> dict:
    """Raises sql_guard.GuardError (code 'forbidden') if the role may not see this list."""
    sqls = LIST_SQL[kind]
    sql = sqls.get(user["role"], sqls["default"])
    r = sql_guard.run_query(sql, user)
    return {"items": r["rows"], "count": r["row_count"], "truncated": r["truncated"]}
