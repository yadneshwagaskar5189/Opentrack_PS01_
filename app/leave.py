"""Trainer leave requests.

A trainer asks for leave (or the owner records it for them). The owner approves it, like every
other change. The leave is saved, and if it falls in the planning week a re-plan with substitutes
is prepared straight away as a new approval (nothing moves until the owner approves that too).
Later dates show "trainer on leave" in the schedule until their week is planned.
"""
from datetime import date, timedelta

from . import config, planner
from .actions import ActionError, _create

MAX_DAYS = 14
DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def can_request(user: dict) -> bool:
    return user["role"] in ("trainer", "owner")


def _parse(value, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        raise ActionError(f"{field} must be a date like 2026-10-21.")


def _pretty(d: date) -> str:
    return d.strftime("%a %d %b")


def propose(conn, user: dict, spec: dict) -> dict:
    if not can_request(user):
        raise ActionError("Only trainers (for themselves) and the owner can request leave.", 403)
    if user["role"] == "trainer":
        tid = user["trainer_id"]
    else:
        tid = spec.get("trainer_id")
        if not tid and spec.get("trainer_name"):
            hit = conn.execute("SELECT id FROM trainers WHERE lower(name) LIKE ?",
                               (f"%{str(spec['trainer_name']).strip().lower()}%",)).fetchall()
            if len(hit) != 1:
                raise ActionError("Say which trainer: no single trainer matches that name.")
            tid = hit[0][0]
    trainer = conn.execute("SELECT id, name FROM trainers WHERE id=?", (tid,)).fetchone() if tid else None
    if trainer is None:
        raise ActionError("Pick the trainer who will be on leave.", 404)
    today = config.today()
    start = _parse(spec.get("start_date") or spec.get("date"), "Start date")
    end = _parse(spec.get("end_date"), "End date") if spec.get("end_date") else start
    if start < today:
        raise ActionError("Leave can't start in the past.")
    if end < start:
        raise ActionError("The last day of leave is before the first day.")
    if (end - start).days >= MAX_DAYS:
        raise ActionError(f"One request can cover at most {MAX_DAYS} days.")
    reason = " ".join(str(spec.get("reason") or "").split())[:200] or "Leave"
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    have = {r[0] for r in conn.execute("SELECT leave_date FROM trainer_leaves WHERE trainer_id=? AND leave_date BETWEEN ? AND ?",
                                       (trainer["id"], start.isoformat(), end.isoformat()))}
    new = [d for d in days if d.isoformat() not in have]
    if not new:
        raise ActionError(f"{trainer['name']} is already on leave on {'that day' if len(days) == 1 else 'those days'}.", 409)
    classes = [dict(r) for r in conn.execute(
        "SELECT class_date, start_time, end_time, batch_code, room_name FROM v_schedule WHERE trainer_id=? "
        "AND class_date BETWEEN ? AND ? ORDER BY class_date, start_time", (trainer["id"], start.isoformat(), end.isoformat()))]
    pws, pwe = config.plan_week()
    in_plan = [c for c in classes if pws.isoformat() <= c["class_date"] <= pwe.isoformat()]
    span = _pretty(start) if start == end else f"{_pretty(start)} to {_pretty(end)}"
    checks = ["Dates are today or later and not already leave"]
    if in_plan:
        checks.append(f"After approval, a re-plan with substitutes is prepared for the {len(in_plan)} class(es) in the "
                      f"planning week ({pws.strftime('%d %b')} to {pwe.strftime('%d %b')})")
    warnings = []
    if have:
        warnings.append("Already on leave (skipped): " + ", ".join(_pretty(date.fromisoformat(d)) for d in sorted(have)))
    later = len(classes) - len(in_plan)
    if later:
        warnings.append(f"{later} class(es) outside the planning week will show 'trainer on leave' until their week is planned")
    preview = {"summary": f"{trainer['name']} on leave {span} ({len(new)} day(s)): {len(classes)} class(es) affected.",
               "fields": [["Trainer", trainer["name"]], ["Dates", span], ["Reason", reason]],
               "table": {"columns": ["Date", "Time", "Batch", "Room"],
                         "rows": [[_pretty(date.fromisoformat(c["class_date"])), f"{c['start_time']}–{c['end_time']}",
                                   c["batch_code"], c["room_name"]] for c in classes]},
               "checks": checks, "warnings": warnings}
    payload = {"trainer_id": trainer["id"], "trainer_name": trainer["name"], "dates": [d.isoformat() for d in new],
               "reason": reason, "preview": preview}
    return _create(conn, user, "request_leave", f"Leave: {trainer['name']}, {span}", payload)


def run(conn, a: dict, edits: dict | None = None) -> dict:
    p = a["payload"]
    added = 0
    for d in p["dates"]:
        if not conn.execute("SELECT 1 FROM trainer_leaves WHERE trainer_id=? AND leave_date=?", (p["trainer_id"], d)).fetchone():
            conn.execute("INSERT INTO trainer_leaves (trainer_id, leave_date, reason) VALUES (?,?,?)",
                         (p["trainer_id"], d, p["reason"]))
            added += 1
    return {"message": f"Leave recorded for {p['trainer_name']}: {added} day(s).", "summary": {"days": added}}


def replan(conn, user: dict, a: dict) -> dict | None:
    """After the leave is approved: a re-plan proposal for the classes it hits in the planning week."""
    p = a["payload"]
    pws, pwe = config.plan_week()
    days = sorted({date.fromisoformat(d).weekday() for d in p["dates"]
                   if pws <= date.fromisoformat(d) <= pwe})
    hits = [d for d in p["dates"] if pws <= date.fromisoformat(d) <= pwe]
    if not days or not conn.execute(
            f"SELECT COUNT(*) FROM v_schedule WHERE trainer_id=? AND class_date IN ({','.join('?' * len(hits))})",
            (p["trainer_id"], *hits)).fetchone()[0]:
        return None
    try:
        out = planner.propose_trainer_unavailable(conn, user, p["trainer_id"], [DAY[d] for d in days], f"leave: {p['reason']}")
    except planner.PlannerError as e:
        raise ActionError(e.message, e.status)
    if not out["proposal"]["changes"] and not out["proposal"]["unscheduled"]:
        return None
    return {"action": out["action"], "note": f"A re-plan with substitutes is waiting for approval (ACT-{out['action']['id']})."}
