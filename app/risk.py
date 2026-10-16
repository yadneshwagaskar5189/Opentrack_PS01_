"""Students at risk of dropping out, with the reasons behind each flag.

Signals: overall absence rate, recent absences, absence streak, missed
assignments, low scores and overdue EMIs. Trainers only get their own
students, and fee signals are removed for them entirely (not just hidden).
"""
from datetime import date, timedelta

from . import config

WEIGHTS = {  # signal -> points
    "absence_rate_high": 30, "absence_rate_mid": 15,
    "recent_absences_high": 20, "recent_absences_mid": 10,
    "streak": 15,
    "missed_assignments_high": 20, "missed_assignments_mid": 8,
    "low_scores": 10,
    "overdue_emis_high": 25, "overdue_emis_mid": 12,
}


class RiskError(Exception):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.message, self.status = message, status


def at_risk(conn, user: dict, min_level: str = "medium") -> dict:
    role = user["role"]
    if role == "accounts":
        raise RiskError("Accounts staff cannot see attendance-based risk. Use the copilot for overdue fees.")
    include_fees = role in ("owner", "counselor")
    today = config.today()
    recent_from = (today - timedelta(days=14)).isoformat()

    where, args = "e.status = 'active'", []
    if role == "trainer":
        where += " AND b.trainer_id = ?"
        args.append(user["trainer_id"])
    enrollments = conn.execute(f"""
        SELECT e.id AS enrollment_id, e.student_id, s.name, e.batch_id, b.code AS batch_code, e.enrolled_on
        FROM enrollments e JOIN students s ON s.id = e.student_id JOIN batches b ON b.id = e.batch_id
        WHERE {where}""", args).fetchall()

    out = []
    for en in enrollments:
        att = conn.execute("""
            SELECT cs.session_date, a.status FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
            WHERE a.student_id = ? AND cs.batch_id = ? ORDER BY cs.session_date""",
                           (en["student_id"], en["batch_id"])).fetchall()
        held = len(att)
        absences = sum(1 for a in att if a["status"] == "absent")
        recent = sum(1 for a in att if a["status"] == "absent" and a["session_date"] >= recent_from)
        streak = 0
        for a in reversed(att):
            if a["status"] != "absent":
                break
            streak += 1
        due_assign = conn.execute("SELECT id FROM assignments WHERE batch_id=? AND due_date < ? AND due_date >= ?",
                                  (en["batch_id"], today.isoformat(), en["enrolled_on"])).fetchall()
        subs = conn.execute("SELECT score FROM submissions WHERE student_id=? AND assignment_id IN "
                            f"({','.join('?' * len(due_assign)) or 'NULL'})",
                            [en["student_id"], *[r["id"] for r in due_assign]]).fetchall()
        missed_assign = len(due_assign) - len(subs)
        scores = [r["score"] for r in subs if r["score"] is not None]
        avg_score = sum(scores) / len(scores) if scores else None

        score, reasons = 0, []
        rate = absences / held if held else 0
        if held >= 4 and rate >= 0.25:
            score += WEIGHTS["absence_rate_high"]
            reasons.append(f"Missed {absences} of {held} classes ({rate:.0%})")
        elif held >= 4 and rate >= 0.15:
            score += WEIGHTS["absence_rate_mid"]
            reasons.append(f"Missed {absences} of {held} classes ({rate:.0%})")
        if recent >= 3:
            score += WEIGHTS["recent_absences_high"]
            reasons.append(f"{recent} absences in the last 2 weeks")
        elif recent == 2:
            score += WEIGHTS["recent_absences_mid"]
            reasons.append("2 absences in the last 2 weeks")
        if streak >= 3:
            score += WEIGHTS["streak"]
            reasons.append(f"Absent for the last {streak} classes in a row")
        if missed_assign >= 2:
            score += WEIGHTS["missed_assignments_high"]
            reasons.append(f"{missed_assign} of {len(due_assign)} assignments not submitted")
        elif missed_assign == 1:
            score += WEIGHTS["missed_assignments_mid"]
            reasons.append(f"1 of {len(due_assign)} assignments not submitted")
        if avg_score is not None and avg_score < 50:
            score += WEIGHTS["low_scores"]
            reasons.append(f"Average assignment score {avg_score:.0f}/100")

        overdue = None
        if include_fees:
            overdue = conn.execute("""
                SELECT COUNT(*) n, COALESCE(SUM(i.amount - i.paid_amount), 0) amt FROM instalments i
                JOIN fee_plans fp ON fp.id = i.fee_plan_id
                WHERE fp.enrollment_id = ? AND i.status IN ('overdue','partial') AND i.due_date < ?""",
                                   (en["enrollment_id"], today.isoformat())).fetchone()
            if overdue["n"] >= 2:
                score += WEIGHTS["overdue_emis_high"]
                reasons.append(f"{overdue['n']} EMIs overdue (₹{overdue['amt']:,})")
            elif overdue["n"] == 1:
                score += WEIGHTS["overdue_emis_mid"]
                reasons.append(f"1 EMI overdue (₹{overdue['amt']:,})")

        score = min(score, 100)
        level = "high" if score >= 50 else "medium" if score >= 30 else "low"
        if level == "low" or (min_level == "high" and level != "high"):
            continue
        out.append({
            "student_id": en["student_id"], "name": en["name"], "batch_code": en["batch_code"],
            "risk_score": score, "level": level, "reasons": reasons,
            "signals": {"classes_held": held, "absences": absences, "absences_last_14_days": recent,
                        "absence_streak": streak, "assignments_due": len(due_assign),
                        "assignments_missed": missed_assign,
                        "avg_assignment_score": round(avg_score, 1) if avg_score is not None else None,
                        **({"overdue_emis": overdue["n"], "overdue_amount": overdue["amt"]} if overdue else {})},
        })
    out.sort(key=lambda r: (-r["risk_score"], r["name"]))
    return {
        "as_of": today.isoformat(),
        "students": out,
        "fee_signals_included": include_fees,
        "method": "Points per signal (max 100): absence rate, absences in last 14 days, absence streak, "
                  "missed assignments, average score below 50" + (", overdue EMIs" if include_fees else "") +
                  ". 50+ = high, 30-49 = medium. A flag is a prompt for a human conversation, not a verdict.",
    }
