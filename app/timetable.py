"""Clash-free weekly timetable planner with minimal-change re-planning.

Hard constraints
  * a trainer teaches one class at a time, only inside their weekly availability,
    and never on a leave day
  * a room hosts one class at a time and must seat the batch's classroom students
  * at most ONLINE_LICENCES online classes run at the same time,
    and a batch's online students must fit its online seat limit
  * a batch meets at most once a day, inside its time window,
    and only on dates between its start and end date

Re-planning keeps every valid slot where it is and moves only what broke,
choosing the nearest free alternative. When a trainer is away, a colleague
with the same skill can cover the class at its usual time (students keep
their slot); otherwise the class moves. Every change comes with a
plain-language reason. Hours are on the 24h clock; 19.5 means 19:30.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import config

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# days: allowed weekdays; starts: preferred start hours; range: earliest start and latest end
WINDOWS = {
    "morning":   {"days": [0, 1, 2, 3, 4, 5], "starts": [8, 10],          "range": (7, 13)},
    "afternoon": {"days": [0, 1, 2, 3, 4, 5], "starts": [13, 15],         "range": (12, 18)},
    "evening":   {"days": [0, 1, 2, 3, 4, 5], "starts": [17, 19],         "range": (16, 21)},
    "weekend":   {"days": [5, 6],             "starts": [9, 11, 14, 16],  "range": (8, 19)},
}

NODE_LIMIT = 200_000
SUBSTITUTE_SAME_SLOT_COST = 1.5     # a colleague covers at the usual time: cheaper than moving day
SUBSTITUTE_COST = 10                # a substitute at a different time: last resort


def window_for(days: list[int], start_hour: float) -> str:
    """Which time window a batch with these usual days and start time belongs to."""
    if days and all(d >= 5 for d in days):
        return "weekend"
    if start_hour < 12:
        return "morning"
    if start_hour < 16:
        return "afternoon"
    return "evening"


def teaches(skills: set[str] | str, course_code: str, course_name: str = "") -> bool:
    """A trainer can teach a course if a skill equals the course code (e.g. 'JFS')
    or appears as a word in the course name (e.g. 'Tally' in 'Tally & GST')."""
    if isinstance(skills, str):
        skills = {x.strip() for x in skills.split(",")}
    name = (course_name or "").lower()
    for sk in skills:
        sk = sk.strip().lower()
        if not sk:
            continue
        if sk == (course_code or "").lower():
            return True
        if name and re.search(r"(?<![a-z0-9])" + re.escape(sk) + r"(?![a-z0-9])", name):
            return True
    return False


def hm(h: float) -> str:
    """19.5 -> '19:30'"""
    mins = int(round(float(h) * 60))
    return f"{mins // 60:02d}:{mins % 60:02d}"


def to_hours(hhmm: str) -> float:
    """'19:30' -> 19.5 (whole hours come back as int)"""
    hh, _, mm = str(hhmm).strip().partition(":")
    v = int(hh) + (int(mm or 0) / 60)
    return int(v) if v == int(v) else round(v, 2)


def _steps(start: float, end: float) -> list[float]:
    """Half-hour steps covered by a class, used for the online licence count."""
    n = int(round((end - start) * 2))
    return [round(start + k * 0.5, 2) for k in range(max(n, 0))]


# ---------------------------------------------------------------- data
@dataclass
class Batch:
    id: int
    code: str
    course_code: str
    trainer_id: int
    mode: str
    time_window: str
    sessions_per_week: int
    session_hours: float
    classroom_size: int
    online_size: int
    online_seat_limit: int
    course_name: str = ""
    active_days: set[int] = field(default_factory=lambda: set(range(7)))   # weekdays inside its dates
    pref_days: list[int] = field(default_factory=list)                      # preferred days (new batches)
    pref_start: float | None = None                                         # preferred start hour

    @property
    def needs_room(self) -> bool:
        return self.mode in ("classroom", "hybrid")

    @property
    def needs_online(self) -> bool:
        return self.mode in ("online", "hybrid")


@dataclass
class Context:
    week_start: date
    batches: dict[int, Batch]
    trainers: dict[int, dict]                      # id -> {name, skills:set}
    availability: dict[int, dict[int, list]]       # trainer -> day -> [(start, end)]
    leave_days: dict[int, set[int]]                # trainer -> {day_of_week}
    leave_reasons: dict[tuple[int, int], str]
    rooms: list[dict]                              # sorted by capacity ascending
    online_licences: int = field(default_factory=lambda: config.ONLINE_LICENCES)

    def trainer_name(self, tid: int) -> str:
        return self.trainers.get(tid, {}).get("name", f"Trainer {tid}")

    def room_name(self, rid: int | None) -> str:
        if rid is None:
            return "Online"
        return next((r["name"] for r in self.rooms if r["id"] == rid), f"Room {rid}")

    def trainer_can(self, tid: int, day: int, start: float, end: float) -> bool:
        if day in self.leave_days.get(tid, set()):
            return False
        return any(s <= start and end <= e for s, e in self.availability.get(tid, {}).get(day, []))

    def can_teach(self, tid: int, b: Batch) -> bool:
        return teaches(self.trainers.get(tid, {}).get("skills", set()), b.course_code, b.course_name)


def fmt_slot(day: int, start: float, end: float) -> str:
    return f"{DAY_NAMES[day]} {hm(start)}–{hm(end)}"


def sessions_needed(b: Batch) -> int:
    """Classes the batch needs this week (fewer if it starts or ends mid-week)."""
    if len(b.active_days) == 7:
        return b.sessions_per_week
    if b.pref_days:
        return len([d for d in b.pref_days if d in b.active_days])
    allowed = [d for d in WINDOWS[b.time_window]["days"] if d in b.active_days]
    return min(b.sessions_per_week, len(allowed))


# ---------------------------------------------------------------- loading
def load_context(conn, week_start: date | None = None,
                 extra_leave_days: dict[int, set[int]] | None = None,
                 extra_leave_reason: str | None = None,
                 new_batches: list[dict] | None = None) -> Context:
    week_start = week_start or config.week_bounds()[0]
    week_end = week_start + timedelta(days=6)

    def active_days(start_date: str | None, end_date: str | None) -> set[int]:
        lo = date.fromisoformat(start_date) if start_date else week_start
        hi = date.fromisoformat(end_date) if end_date else week_end
        return {d for d in range(7) if lo <= week_start + timedelta(days=d) <= hi}

    def day_list(days: str | None) -> list[int]:
        out = []
        for part in re.split(r"[,|/ ]+", days or ""):
            if part.strip()[:3].title() in DAY_NAMES:
                out.append(DAY_NAMES.index(part.strip()[:3].title()))
        return out

    batches: dict[int, Batch] = {}
    rows = conn.execute("""
        SELECT b.*, c.code AS course_code, c.name AS course_name,
          (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
              AND e.learning_mode='classroom') AS classroom_size,
          (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
              AND e.learning_mode='online') AS online_size
        FROM batches b JOIN courses c ON c.id=b.course_id
        WHERE b.status IN ('active','upcoming') AND b.start_date <= ? AND b.end_date >= ?
        ORDER BY b.id""", (week_end.isoformat(), week_start.isoformat())).fetchall()
    for r in rows:
        keys = r.keys()
        b = Batch(r["id"], r["code"], r["course_code"], r["trainer_id"], r["mode"],
                  r["time_window"], r["sessions_per_week"], r["session_hours"],
                  r["classroom_size"], r["online_size"], r["online_seat_limit"],
                  course_name=r["course_name"], active_days=active_days(r["start_date"], r["end_date"]))
        if "days" in keys and r["days"]:
            b.pref_days = day_list(r["days"])
        if "start_time" in keys and r["start_time"]:
            b.pref_start = to_hours(r["start_time"])
        if sessions_needed(b) > 0:
            batches[r["id"]] = b
    for nb in new_batches or []:
        b = Batch(nb["id"], nb["code"], nb["course_code"], nb["trainer_id"],
                  nb["mode"], nb["time_window"], nb["sessions_per_week"],
                  nb.get("session_hours", 2), nb.get("classroom_size", 0),
                  nb.get("online_size", 0), nb.get("online_seat_limit", 0),
                  course_name=nb.get("course_name", ""),
                  active_days=active_days(nb.get("start_date"), nb.get("end_date")),
                  pref_days=list(nb.get("preferred_days") or []), pref_start=nb.get("preferred_start"))
        batches[nb["id"]] = b

    trainers = {r["id"]: {"name": r["name"], "skills": {s.strip() for s in (r["skills"] or "").split(",") if s.strip()}}
                for r in conn.execute("SELECT id, name, skills FROM trainers")}
    availability: dict[int, dict[int, list]] = {}
    for r in conn.execute("SELECT trainer_id, day_of_week, start_hour, end_hour FROM trainer_availability"):
        availability.setdefault(r["trainer_id"], {}).setdefault(r["day_of_week"], []).append(
            (r["start_hour"], r["end_hour"]))

    leave_days: dict[int, set[int]] = {}
    leave_reasons: dict[tuple[int, int], str] = {}
    for r in conn.execute("SELECT trainer_id, leave_date, reason FROM trainer_leaves "
                          "WHERE leave_date BETWEEN ? AND ?", (week_start.isoformat(), week_end.isoformat())):
        d = date.fromisoformat(r["leave_date"]).weekday()
        leave_days.setdefault(r["trainer_id"], set()).add(d)
        leave_reasons[(r["trainer_id"], d)] = r["reason"] or "on leave"
    for tid, days in (extra_leave_days or {}).items():
        for d in days:
            leave_days.setdefault(tid, set()).add(d)
            leave_reasons[(tid, d)] = extra_leave_reason or "unavailable"

    rooms = [dict(r) for r in conn.execute("SELECT id, name, capacity FROM rooms ORDER BY capacity, id")]
    return Context(week_start, batches, trainers, availability, leave_days, leave_reasons, rooms)


def load_slots(conn, week_start: date) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, batch_id, trainer_id, room_id, day_of_week, start_hour, end_hour, is_online "
        "FROM timetable_slots WHERE week_start=? ORDER BY day_of_week, start_hour, batch_id",
        (week_start.isoformat(),))]


# ---------------------------------------------------------------- validation
def _overlap(a: dict, b: dict) -> bool:
    return (a["day_of_week"] == b["day_of_week"]
            and a["start_hour"] < b["end_hour"] and b["start_hour"] < a["end_hour"])


def find_violations(ctx: Context, slots: list[dict]) -> list[dict]:
    """Every broken hard constraint in a timetable, with the slots involved."""
    out: list[dict] = []
    for i, s in enumerate(slots):
        b = ctx.batches.get(s["batch_id"])
        if b is None:
            continue
        where = f"{b.code} {fmt_slot(s['day_of_week'], s['start_hour'], s['end_hour'])}"
        if s["day_of_week"] in ctx.leave_days.get(s["trainer_id"], set()):
            reason = ctx.leave_reasons.get((s["trainer_id"], s["day_of_week"]), "on leave")
            out.append({"type": "trainer_on_leave", "slots": [i],
                        "message": f"{where}: {ctx.trainer_name(s['trainer_id'])} is unavailable on "
                                   f"{DAY_NAMES[s['day_of_week']]} ({reason})"})
        elif not ctx.trainer_can(s["trainer_id"], s["day_of_week"], s["start_hour"], s["end_hour"]):
            out.append({"type": "trainer_unavailable", "slots": [i],
                        "message": f"{where}: outside {ctx.trainer_name(s['trainer_id'])}'s availability"})
        if b.needs_room:
            room = next((r for r in ctx.rooms if r["id"] == s["room_id"]), None)
            if room is None:
                out.append({"type": "no_room", "slots": [i], "message": f"{where}: no classroom assigned"})
            elif room["capacity"] < b.classroom_size:
                out.append({"type": "room_too_small", "slots": [i],
                            "message": f"{where}: {room['name']} seats {room['capacity']} but {b.classroom_size} classroom students attend"})
        if b.needs_online and b.online_size > b.online_seat_limit:
            out.append({"type": "online_seats", "slots": [i],
                        "message": f"{b.code}: {b.online_size} online students exceed its online seat limit of {b.online_seat_limit}"})
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            a, c = slots[i], slots[j]
            if not _overlap(a, c):
                continue
            ba, bc = ctx.batches.get(a["batch_id"]), ctx.batches.get(c["batch_id"])
            if ba is None or bc is None:
                continue
            when = fmt_slot(a["day_of_week"], max(a["start_hour"], c["start_hour"]), min(a["end_hour"], c["end_hour"]))
            if a["trainer_id"] == c["trainer_id"]:
                out.append({"type": "trainer_double_booked", "slots": [i, j],
                            "message": f"{ctx.trainer_name(a['trainer_id'])} is double-booked {when}: {ba.code} and {bc.code}"})
            if a["room_id"] is not None and a["room_id"] == c["room_id"]:
                out.append({"type": "room_double_booked", "slots": [i, j],
                            "message": f"{ctx.room_name(a['room_id'])} is double-booked {when}: {ba.code} and {bc.code}"})
            if a["batch_id"] == c["batch_id"]:
                out.append({"type": "batch_overlap", "slots": [i, j],
                            "message": f"{ba.code} has two classes at once {when}"})
    # online licence concurrency, checked every half hour (reported once per group of classes)
    reported: set[tuple] = set()
    for day in range(7):
        for t in (k * 0.5 for k in range(48)):
            live = [i for i, s in enumerate(slots) if s["is_online"] and s["day_of_week"] == day
                    and s["start_hour"] <= t < s["end_hour"]]
            if len(live) > ctx.online_licences and tuple(live) not in reported:
                reported.add(tuple(live))
                codes = ", ".join(ctx.batches[slots[i]["batch_id"]].code for i in live if slots[i]["batch_id"] in ctx.batches)
                out.append({"type": "online_licences", "slots": live,
                            "message": f"{len(live)} online classes at {DAY_NAMES[day]} {hm(t)} ({codes}) but only {ctx.online_licences} licences"})
    # same batch twice on one day
    seen: dict[tuple[int, int], int] = {}
    for i, s in enumerate(slots):
        key = (s["batch_id"], s["day_of_week"])
        if key in seen and not _overlap(slots[seen[key]], s):
            b = ctx.batches.get(s["batch_id"])
            out.append({"type": "batch_twice_a_day", "slots": [seen[key], i],
                        "message": f"{b.code if b else s['batch_id']} meets twice on {DAY_NAMES[s['day_of_week']]}"})
        seen.setdefault(key, i)
    return out


# ---------------------------------------------------------------- solver
class _State:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.trainer_busy: dict[tuple[int, int], list] = {}
        self.room_busy: dict[tuple[int, int], list] = {}
        self.online: dict[tuple[int, int], int] = {}
        self.batch_days: dict[int, set[int]] = {}

    @staticmethod
    def _free(busy: list, start: int, end: int) -> bool:
        return all(end <= s or e <= start for s, e in busy)

    def fits(self, b: Batch, tid: int, day: int, start: float, room_id: int | None) -> bool:
        end = start + b.session_hours
        if day not in b.active_days or day in self.batch_days.get(b.id, set()):
            return False
        if not self.ctx.trainer_can(tid, day, start, end):
            return False
        if not self._free(self.trainer_busy.get((tid, day), []), start, end):
            return False
        if b.needs_room:
            if room_id is None or not self._free(self.room_busy.get((room_id, day), []), start, end):
                return False
        if b.needs_online and any(self.online.get((day, h), 0) >= self.ctx.online_licences
                                  for h in _steps(start, end)):
            return False
        return True

    def place(self, b: Batch, tid: int, day: int, start: float, room_id: int | None) -> None:
        end = start + b.session_hours
        self.trainer_busy.setdefault((tid, day), []).append((start, end))
        if b.needs_room:
            self.room_busy.setdefault((room_id, day), []).append((start, end))
        if b.needs_online:
            for h in _steps(start, end):
                self.online[(day, h)] = self.online.get((day, h), 0) + 1
        self.batch_days.setdefault(b.id, set()).add(day)

    def remove(self, b: Batch, tid: int, day: int, start: float, room_id: int | None) -> None:
        end = start + b.session_hours
        self.trainer_busy[(tid, day)].remove((start, end))
        if b.needs_room:
            self.room_busy[(room_id, day)].remove((start, end))
        if b.needs_online:
            for h in _steps(start, end):
                self.online[(day, h)] -= 1
        self.batch_days[b.id].discard(day)


@dataclass
class _Session:
    batch: Batch
    hint: dict | None          # original slot, if any (for minimal change)
    origin_id: int | None      # id of the original slot row
    problem: str | None        # why it has to move
    pref: dict | None = None   # preferred day/start for a new class (no original slot)


def _rooms_for(ctx: Context, b: Batch) -> list[int | None]:
    if not b.needs_room:
        return [None]
    return [r["id"] for r in ctx.rooms if r["capacity"] >= b.classroom_size]


def _starts(b: Batch, *extra: float | None) -> tuple[list[float], set[float]]:
    """Possible start hours inside the batch's window, and the preferred ones (no penalty)."""
    win = WINDOWS[b.time_window]
    lo, hi = win["range"]
    preferred = {float(x) for x in win["starts"]} | {float(x) for x in extra if x is not None}
    if b.pref_start is not None:
        preferred.add(float(b.pref_start))
    options = {float(h) for h in range(int(lo), int(hi) + 1)} | preferred
    starts = sorted(_num(x) for x in options if lo <= x and x + b.session_hours <= hi)
    return starts, preferred


def _num(x: float):
    """8.0 -> 8, 19.5 stays 19.5 (keeps whole hours as ints in JSON and the database)."""
    return int(x) if float(x) == int(x) else round(float(x), 2)


def _move_cost(ref: dict, day: int, start: float, rid) -> float:
    same_day, same_start = day == ref["day_of_week"], start == ref["start_hour"]
    cost = 0 if (same_day and same_start) else 1 if same_day else 2 if same_start else 3
    cost += abs(day - ref["day_of_week"]) * 0.1 + abs(start - ref["start_hour"]) * 0.05
    if ref.get("room_id") is not None and rid != ref.get("room_id"):
        cost += 0.05
    return cost


def _candidates(st: _State, s: _Session, allow_substitute: bool) -> list[tuple]:
    ctx, b, hint = st.ctx, s.batch, s.hint
    win = WINDOWS[b.time_window]
    ref = hint or s.pref
    trainers = [b.trainer_id]
    if allow_substitute:
        trainers += sorted(t for t in ctx.trainers if t != b.trainer_id and ctx.can_teach(t, b))
    hint_start = hint["start_hour"] if hint and hint.get("day_of_week") is not None else None
    starts, preferred = _starts(b, hint_start, s.pref["start_hour"] if s.pref else None)
    out = []
    for tid in trainers:
        for day in win["days"]:
            if day not in b.active_days:
                continue
            for start in starts:
                for rid in _rooms_for(ctx, b):
                    if not st.fits(b, tid, day, start, rid):
                        continue
                    cost = 0.0
                    if start not in preferred:
                        cost += 0.3                                  # prefer the usual start times
                    substitute = tid != b.trainer_id
                    if ref:
                        move = _move_cost(ref, day, start, rid)
                        if substitute:                               # same slot, colleague covers it
                            cost += (SUBSTITUTE_SAME_SLOT_COST if move < 0.1 else SUBSTITUTE_COST) + move
                        else:
                            cost += move
                    elif substitute:
                        cost += SUBSTITUTE_COST
                    days = st.batch_days.get(b.id, set())
                    if any(abs(day - d) == 1 for d in days):
                        cost += 0.5                                  # spread classes across the week
                    if rid is not None:
                        cap = next(r["capacity"] for r in ctx.rooms if r["id"] == rid)
                        cost += (cap - b.classroom_size) / 1000      # tightest room that fits
                    out.append((cost, day, start, rid, tid))
    out.sort()
    return out


def _solve(st: _State, sessions: list[_Session], allow_substitute: bool) -> list[tuple] | None:
    """Backtracking search. Returns one (day, start, room, trainer) per session, or None."""
    order = sorted(range(len(sessions)), key=lambda i: len(_candidates(st, sessions[i], allow_substitute)))
    result: list[tuple | None] = [None] * len(sessions)
    nodes = 0

    def rec(k: int) -> bool:
        nonlocal nodes
        if k == len(order):
            return True
        i = order[k]
        s = sessions[i]
        for cost, day, start, rid, tid in _candidates(st, s, allow_substitute):
            nodes += 1
            if nodes > NODE_LIMIT:
                return False
            st.place(s.batch, tid, day, start, rid)
            result[i] = (day, start, rid, tid)
            if rec(k + 1):
                return True
            st.remove(s.batch, tid, day, start, rid)
            result[i] = None
        return False

    return result if rec(0) else None  # type: ignore[return-value]


def _why_unplaceable(ctx: Context, st: _State, b: Batch) -> str:
    win = WINDOWS[b.time_window]
    if b.needs_room and not _rooms_for(ctx, b):
        return f"no room seats {b.classroom_size} classroom students"
    starts, _ = _starts(b)
    if not any(ctx.trainer_can(b.trainer_id, d, s, s + b.session_hours)
               for d in win["days"] if d in b.active_days for s in starts):
        return (f"{ctx.trainer_name(b.trainer_id)} has no availability in the {b.time_window} window this week "
                f"and no free substitute who teaches {b.course_name or b.course_code}")
    return (f"every {b.time_window} slot this week clashes with the trainer's other classes, "
            f"room bookings or the {ctx.online_licences} online licences")


def _slot(b: Batch, day: int, start: float, rid: int | None, tid: int) -> dict:
    return {"batch_id": b.id, "trainer_id": tid, "room_id": rid, "day_of_week": day,
            "start_hour": _num(start), "end_hour": _num(start + b.session_hours), "is_online": int(b.needs_online)}


def plan(ctx: Context, existing: list[dict] | None = None, allow_substitute: bool = True) -> dict:
    """Plan the week. Keeps valid existing slots fixed and re-places only what broke."""
    t0 = time.perf_counter()
    existing = [dict(s) for s in (existing or []) if s["batch_id"] in ctx.batches]

    # 1. Which existing slots are broken?
    problems: dict[int, str] = {}
    for v in find_violations(ctx, existing):
        idxs = v["slots"]
        if v["type"] in ("trainer_double_booked", "room_double_booked", "batch_overlap",
                         "batch_twice_a_day", "online_licences"):
            idxs = idxs[1:]          # keep the first, move the others
        if v["type"] == "online_seats":
            continue                 # a capacity warning, not something moving fixes
        for i in idxs:
            problems.setdefault(i, v["message"])

    # 2. Sessions each batch needs this week
    by_batch: dict[int, list[int]] = {}
    for i, s in enumerate(existing):
        by_batch.setdefault(s["batch_id"], []).append(i)
    removed: list[dict] = []
    fixed: list[int] = []
    to_place: list[_Session] = []
    for bid, b in ctx.batches.items():
        need = sessions_needed(b)
        idxs = sorted(by_batch.get(bid, []), key=lambda i: (i in problems, existing[i]["day_of_week"]))
        for i in idxs[need:]:
            removed.append(existing[i])
        for i in idxs[:need]:
            if i in problems:
                to_place.append(_Session(b, existing[i], existing[i].get("id"), problems[i]))
            else:
                fixed.append(i)
        used = {existing[i]["day_of_week"] for i in idxs[:need]}
        free_prefs = [d for d in b.pref_days if d not in used and d in b.active_days]
        for k in range(need - min(len(idxs), need)):
            pref = None
            if k < len(free_prefs):
                pref = {"day_of_week": free_prefs[k], "room_id": None,
                        "start_hour": b.pref_start if b.pref_start is not None else _starts(b)[0][0]}
            to_place.append(_Session(b, None, None, None, pref))

    # 3. Local repair: everything valid stays put
    st = _State(ctx)
    for i in fixed:
        s = existing[i]
        st.place(ctx.batches[s["batch_id"]], s["trainer_id"], s["day_of_week"], s["start_hour"], s["room_id"])
    placed = _solve(st, to_place, allow_substitute)
    strategy = "local_repair"
    final_fixed = [existing[i] for i in fixed]

    # 4. If that is impossible, re-plan the whole week, staying close to the old one
    if placed is None:
        strategy = "full_replan"
        st = _State(ctx)
        everything = [_Session(ctx.batches[existing[i]["batch_id"]], existing[i], existing[i].get("id"), None)
                      for i in fixed] + to_place
        placed = _solve(st, everything, allow_substitute)
        if placed is not None:
            to_place, final_fixed = everything, []

    unscheduled: list[dict] = []
    new_slots: list[dict] = [dict(s, origin_id=s.get("id")) for s in final_fixed]
    if placed is None:
        # 5. Last resort: greedy, report what could not be placed
        strategy = "partial"
        st = _State(ctx)
        for s in final_fixed:
            st.place(ctx.batches[s["batch_id"]], s["trainer_id"], s["day_of_week"], s["start_hour"], s["room_id"])
        placed = []
        for s in to_place:
            cands = _candidates(st, s, allow_substitute)
            if cands:
                _, day, start, rid, tid = cands[0]
                st.place(s.batch, tid, day, start, rid)
                placed.append((day, start, rid, tid))
            else:
                placed.append(None)
                unscheduled.append({"batch_code": s.batch.code,
                                    "reason": _why_unplaceable(ctx, st, s.batch)})

    changes: list[dict] = []
    for s, p in zip(to_place, placed):
        if p is None:
            continue
        day, start, rid, tid = p
        slot = _slot(s.batch, day, start, rid, tid)
        slot["origin_id"] = s.origin_id
        new_slots.append(slot)
        changes.extend(_describe(ctx, s, slot))
    for r in removed:
        b = ctx.batches[r["batch_id"]]
        changes.append({"batch_code": b.code, "type": "removed",
                        "from": fmt_slot(r["day_of_week"], r["start_hour"], r["end_hour"]), "to": None,
                        "reason": f"{b.code} needs only {sessions_needed(b)} class(es) this week"})

    new_slots.sort(key=lambda s: (s["day_of_week"], s["start_hour"], s["batch_id"]))
    remaining = find_violations(ctx, new_slots)
    return {
        "week_start": ctx.week_start.isoformat(),
        "strategy": strategy,
        "slots": [_present(ctx, s) for s in new_slots],
        "changes": changes,
        "unscheduled": unscheduled,
        "problems_found": sorted(set(problems.values())),
        "violations_after": [v["message"] for v in remaining],
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def _describe(ctx: Context, s: _Session, new: dict) -> list[dict]:
    b = s.batch
    to = fmt_slot(new["day_of_week"], new["start_hour"], new["end_hour"])
    where = ctx.room_name(new["room_id"])
    if s.hint is None:
        room_note = ""
        if new["room_id"] is not None:
            cap = next(r["capacity"] for r in ctx.rooms if r["id"] == new["room_id"])
            room_note = f"; {where} is the smallest free room that seats its {b.classroom_size} classroom students ({cap} seats)"
        if s.pref and (s.pref["day_of_week"], s.pref["start_hour"]) == (new["day_of_week"], new["start_hour"]):
            why = (f"the requested {fmt_slot(new['day_of_week'], new['start_hour'], new['end_hour'])} slot is free "
                   f"for {ctx.trainer_name(new['trainer_id'])}, a room and an online licence")
        elif s.pref:
            want = fmt_slot(s.pref["day_of_week"], s.pref["start_hour"], s.pref["start_hour"] + b.session_hours)
            why = f"the requested {want} was not free, so this is the nearest slot where trainer, room and online licence are all available"
        else:
            why = (f"{b.code} needs {sessions_needed(b)} class(es) this week; this is a free {b.time_window} slot "
                   f"where trainer, room and online licence are all available")
        if b.needs_online:
            why += f"; {b.online_size} online student(s) fit its online seat limit of {b.online_seat_limit}"
        return [{"batch_code": b.code, "type": "added", "from": None, "to": f"{to} ({where})",
                 "reason": why + room_note}]
    h = s.hint
    same_time = (h["day_of_week"], h["start_hour"]) == (new["day_of_week"], new["start_hour"])
    same_room = h.get("room_id") == new["room_id"]
    same_trainer = h["trainer_id"] == new["trainer_id"]
    if same_time and same_room and same_trainer:
        return []
    frm = f"{fmt_slot(h['day_of_week'], h['start_hour'], h['end_hour'])} ({ctx.room_name(h.get('room_id'))})"
    parts = []
    if s.problem:
        parts.append(s.problem)
    if not same_trainer and same_time:
        parts.append(f"{ctx.trainer_name(new['trainer_id'])} also teaches {b.course_name or b.course_code} and is free "
                     f"then, so students keep their usual time")
    elif not same_trainer:
        parts.append(f"{ctx.trainer_name(new['trainer_id'])} substitutes because "
                     f"{ctx.trainer_name(h['trainer_id'])} has no free {b.time_window} slot this week")
    if not same_time:
        parts.append("moved to the nearest slot where trainer, room and online licence are all free")
    elif not same_room:
        parts.append(f"same time, moved to {where}, which is free and seats {b.classroom_size}")
    kind = "trainer_changed" if not same_trainer and same_time else "moved"
    if s.problem is None and kind == "moved":
        parts.append("re-planned to make room for the other changes")
    return [{"batch_code": b.code, "type": kind, "from": frm,
             "to": f"{to} ({where})" + ("" if same_trainer else f" with {ctx.trainer_name(new['trainer_id'])}"),
             "reason": "; ".join(parts)}]


def _present(ctx: Context, s: dict) -> dict:
    b = ctx.batches[s["batch_id"]]
    return {
        "batch_id": s["batch_id"], "batch_code": b.code, "course_name": b.course_name,
        "trainer_id": s["trainer_id"], "trainer_name": ctx.trainer_name(s["trainer_id"]),
        "room_id": s["room_id"], "room_name": ctx.room_name(s["room_id"]),
        "day_of_week": s["day_of_week"], "day_name": DAY_NAMES[s["day_of_week"]],
        "start_hour": s["start_hour"], "end_hour": s["end_hour"], "is_online": s["is_online"],
        "mode": b.mode,
    }


def save_slots(conn, week_start: date, slots: list[dict]) -> None:
    """Replace a week's timetable. Call inside a transaction."""
    conn.execute("DELETE FROM timetable_slots WHERE week_start=?", (week_start.isoformat(),))
    conn.executemany(
        "INSERT INTO timetable_slots (week_start, batch_id, trainer_id, room_id, day_of_week, "
        "start_hour, end_hour, is_online) VALUES (?,?,?,?,?,?,?,?)",
        [(week_start.isoformat(), s["batch_id"], s["trainer_id"], s["room_id"], s["day_of_week"],
          s["start_hour"], s["end_hour"], s["is_online"]) for s in slots])


def usual_slots(conn, week_start: date) -> list[dict]:
    """A week built from each batch's usual days, times, trainer and room (batches.days/start_time)."""
    week_end = week_start + timedelta(days=6)
    out = []
    for r in conn.execute("SELECT * FROM batches WHERE status IN ('active','upcoming') AND days IS NOT NULL "
                          "AND start_time IS NOT NULL AND start_date <= ? AND end_date >= ? ORDER BY id",
                          (week_end.isoformat(), week_start.isoformat())):
        start, end = to_hours(r["start_time"]), to_hours(r["end_time"] or "")
        for name in re.split(r"[,|/ ]+", r["days"]):
            if name[:3].title() not in DAY_NAMES:
                continue
            d = DAY_NAMES.index(name[:3].title())
            if not r["start_date"] <= (week_start + timedelta(days=d)).isoformat() <= r["end_date"]:
                continue
            out.append({"batch_id": r["id"], "trainer_id": r["trainer_id"],
                        "room_id": r["room_id"] if r["mode"] != "online" else None,
                        "day_of_week": d, "start_hour": start, "end_hour": end,
                        "is_online": int(r["mode"] in ("online", "hybrid"))})
    return out


def ensure_week(conn, week_start: date) -> int:
    """If the week has no timetable yet, fill it from the batches' usual pattern. Returns the slot count."""
    have = conn.execute("SELECT COUNT(*) FROM timetable_slots WHERE week_start=?", (week_start.isoformat(),)).fetchone()[0]
    if have:
        return have
    slots = usual_slots(conn, week_start)
    if slots:
        save_slots(conn, week_start, slots)
        conn.commit()
    return len(slots)
