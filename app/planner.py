"""Timetable features used by the API and the copilot. Every change is returned
as a preview (pending action) that the owner approves before it is saved."""
from datetime import date, timedelta

from . import actions, config, timetable

DAY_LOOKUP = {name.lower(): i for i, name in enumerate(timetable.DAY_NAMES)}
DAY_LOOKUP.update({"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
                   "saturday": 5, "sunday": 6})


class PlannerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def parse_days(days) -> list[int]:
    out = []
    for d in days or []:
        if isinstance(d, int) or (isinstance(d, str) and d.strip().isdigit()):
            n = int(d)
        else:
            s = str(d).strip().lower()
            if len(s) == 10 and s[4] == "-":
                n = date.fromisoformat(s).weekday()
            elif s in DAY_LOOKUP:
                n = DAY_LOOKUP[s]
            elif s[:3] in DAY_LOOKUP:
                n = DAY_LOOKUP[s[:3]]
            else:
                raise PlannerError(f"Unknown day: {d}")
        if not 0 <= n <= 6:
            raise PlannerError(f"Day must be 0 (Mon) to 6 (Sun), got {n}")
        out.append(n)
    if not out:
        raise PlannerError("Give at least one day.")
    return sorted(set(out))


def current(conn, user) -> dict:
    if user["role"] == "accounts":
        raise PlannerError("Accounts staff cannot see the timetable.", 403)
    week_start = config.week_bounds()[0]
    ctx = timetable.load_context(conn, week_start)
    slots = timetable.load_slots(conn, week_start)
    violations = timetable.find_violations(ctx, slots)
    presented = [dict(timetable._present(ctx, s), id=s["id"]) for s in slots if s["batch_id"] in ctx.batches]
    clash_ids = {slots[i]["id"] for v in violations for i in v["slots"]}
    for s in presented:
        s["has_problem"] = s["id"] in clash_ids
    if user["role"] == "trainer":
        presented = [s for s in presented if s["trainer_id"] == user["trainer_id"]]
        violations = [v for v in violations if any(slots[i]["trainer_id"] == user["trainer_id"] for i in v["slots"])]
    return {
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "days": timetable.DAY_NAMES,
        "slots": presented,
        "problems": [v["message"] for v in violations],
        "online_licences": ctx.online_licences,
    }


def propose_fix(conn, user) -> dict:
    week_start = config.week_bounds()[0]
    ctx = timetable.load_context(conn, week_start)
    existing = timetable.load_slots(conn, week_start)
    result = timetable.plan(ctx, existing)
    if not result["changes"]:
        return {"message": "No clashes found. The timetable is already valid.", "proposal": result, "action": None}
    action = actions.propose_timetable(conn, user, result, f"Fix {len(result['problems_found'])} timetable problem(s)")
    return {"message": f"Found {len(result['problems_found'])} problem(s); {len(result['changes'])} change(s) proposed "
                       f"in {result['elapsed_ms']} ms.", "proposal": result, "action": action}


def propose_trainer_unavailable(conn, user, trainer_id: int, days, reason: str | None = None) -> dict:
    trainer = conn.execute("SELECT id, name FROM trainers WHERE id=?", (trainer_id,)).fetchone()
    if trainer is None:
        raise PlannerError("Trainer not found.", 404)
    day_list = parse_days(days)
    week_start = config.week_bounds()[0]
    reason = reason or "unavailable"
    ctx = timetable.load_context(conn, week_start, extra_leave_days={trainer_id: set(day_list)},
                                 extra_leave_reason=reason)
    result = timetable.plan(ctx, timetable.load_slots(conn, week_start))
    leaves = [{"trainer_id": trainer_id, "leave_date": (week_start + timedelta(days=d)).isoformat(), "reason": reason}
              for d in day_list]
    names = ", ".join(timetable.DAY_NAMES[d] for d in day_list)
    title = f"{trainer['name']} unavailable on {names}: re-planned timetable"
    action = actions.propose_timetable(conn, user, result, title, leaves=leaves)
    msg = (f"{trainer['name']} is marked unavailable on {names} ({reason}). {len(result['changes'])} change(s) proposed "
           f"in {result['elapsed_ms']} ms.")
    if result["unscheduled"]:
        msg += f" {len(result['unscheduled'])} class(es) could not be placed; see 'unscheduled'."
    if not result["changes"] and not result["unscheduled"]:
        msg += " None of their classes fall on those days, so nothing moves."
    return {"message": msg, "proposal": result, "action": action}


def propose_new_batch(conn, user, spec: dict) -> dict:
    code = (spec.get("code") or "").strip().upper()
    if not code:
        raise PlannerError("code is required, e.g. DEVOPS-02")
    if conn.execute("SELECT 1 FROM batches WHERE code=?", (code,)).fetchone():
        raise PlannerError(f"A batch called {code} already exists.", 409)
    course = conn.execute("SELECT code, duration_weeks FROM courses WHERE code=?",
                          ((spec.get("course_code") or "").upper(),)).fetchone()
    if course is None:
        raise PlannerError("Unknown course_code. Use one of: " +
                           ", ".join(r[0] for r in conn.execute("SELECT code FROM courses")))
    trainer = conn.execute("SELECT id, name, skills FROM trainers WHERE id=?", (spec.get("trainer_id"),)).fetchone()
    if trainer is None:
        raise PlannerError("Unknown trainer_id.")
    if course["code"] not in [s.strip() for s in trainer["skills"].split(",")]:
        raise PlannerError(f"{trainer['name']} does not teach {course['code']} (skills: {trainer['skills']}).")
    mode = spec.get("mode", "classroom")
    window = spec.get("time_window", "evening")
    if mode not in ("classroom", "online", "hybrid"):
        raise PlannerError("mode must be classroom, online or hybrid.")
    if window not in timetable.WINDOWS:
        raise PlannerError("time_window must be morning, afternoon, evening or weekend.")
    spw = int(spec.get("sessions_per_week", 2))
    if not 1 <= spw <= 6:
        raise PlannerError("sessions_per_week must be 1 to 6.")
    week_start = config.week_bounds()[0]
    start = date.fromisoformat(spec["start_date"]) if spec.get("start_date") else week_start
    nb = {
        "id": -1, "code": code, "course_code": course["code"], "trainer_id": trainer["id"], "mode": mode,
        "time_window": window, "sessions_per_week": spw, "session_hours": 2,
        "classroom_size": int(spec.get("classroom_size", 0 if mode == "online" else 15)),
        "online_size": int(spec.get("online_size", 0 if mode == "classroom" else 10)),
        "online_seat_limit": int(spec.get("online_seat_limit", 0 if mode == "classroom" else 30)),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(weeks=course["duration_weeks"])).isoformat(),
    }
    ctx = timetable.load_context(conn, week_start, new_batches=[nb])
    result = timetable.plan(ctx, timetable.load_slots(conn, week_start))
    action = actions.propose_timetable(conn, user, result, f"Add batch {code} to the timetable", new_batch=nb)
    msg = f"Placed {code} with {len([c for c in result['changes'] if c['batch_code'] == code])} class(es) in {result['elapsed_ms']} ms."
    if result["unscheduled"]:
        msg += f" Could not place {len(result['unscheduled'])} class(es): {result['unscheduled'][0]['reason']}."
    return {"message": msg, "proposal": result, "action": action}
