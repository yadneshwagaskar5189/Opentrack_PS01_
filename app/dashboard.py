"""Role-aware dashboard numbers and simple lists for the frontend.

Lists go through sql_guard with fixed SQL, so they obey exactly the same
role rules as the copilot."""
from datetime import timedelta

from . import actions, config, planner, risk, sql_guard


def _one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def summary(conn, user: dict) -> dict:
    role = user["role"]
    today = config.today()
    ws, we = config.due_window()            # "this week" for fees: today + 6 days

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
        return len(actions.list_actions(conn, user, "pending"))      # what this user can see in Approvals

    def classes_today(trainer_id=None):
        # today's real date (v_schedule), not the planning week (which is next week from Friday on)
        sql = ("SELECT start_time, end_time, batch_code, trainer_name, room_name, mode, trainer_on_leave "
               "FROM v_schedule WHERE class_date = ?")
        args: list = [today.isoformat()]
        if trainer_id:
            sql += " AND trainer_id = ?"
            args.append(trainer_id)
        hours = lambda t: int(t[:2]) + int(t[3:5]) / 60                                  # noqa: E731
        return [{"start_hour": hours(r["start_time"]), "end_hour": hours(r["end_time"]), "batch_code": r["batch_code"],
                 "trainer_name": r["trainer_name"], "room_name": None if r["room_name"] == "Online" else r["room_name"],
                 "is_online": 1 if r["mode"] in ("online", "hybrid") else 0, "trainer_on_leave": r["trainer_on_leave"]}
                for r in conn.execute(sql + " ORDER BY start_time", args)]

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
    "courses": {
        "trainer": "SELECT id, code, name, duration_weeks FROM v_courses_public ORDER BY code",
        "default": "SELECT id, code, name, duration_weeks FROM v_courses_with_fee ORDER BY code",
    },
}


def list_items(user: dict, kind: str) -> dict:
    """Raises sql_guard.GuardError (code 'forbidden') if the role may not see this list."""
    sqls = LIST_SQL[kind]
    sql = sqls.get(user["role"], sqls["default"])
    r = sql_guard.run_query(sql, user)
    return {"items": r["rows"], "count": r["row_count"], "truncated": r["truncated"]}
