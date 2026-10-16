"""Clash-free weekly timetable planner with minimal-change re-planning.

Hard constraints
  * a trainer teaches one class at a time, only inside their weekly availability,
    and never on a leave day
  * a room hosts one class at a time and must seat the batch's classroom students
  * at most ONLINE_LICENCES online classes run at the same time,
    and a batch's online students must fit its online seat limit
  * a batch meets at most once a day, inside its time window

Re-planning keeps every valid slot where it is and moves only what broke,
choosing the nearest free alternative. If a trainer has no free slot left,
a substitute trainer with the same course skill is used. Every change comes
with a plain-language reason.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import config

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

WINDOWS = {
    "morning":   {"days": [0, 1, 2, 3, 4, 5], "starts": [8, 10]},
    "afternoon": {"days": [0, 1, 2, 3, 4, 5], "starts": [13, 15]},
    "evening":   {"days": [0, 1, 2, 3, 4, 5], "starts": [17, 19]},
    "weekend":   {"days": [5, 6],             "starts": [9, 11, 14, 16]},
}

NODE_LIMIT = 200_000


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
    session_hours: int
    classroom_size: int
    online_size: int
    online_seat_limit: int

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

    def trainer_can(self, tid: int, day: int, start: int, end: int) -> bool:
        if day in self.leave_days.get(tid, set()):
            return False
        return any(s <= start and end <= e for s, e in self.availability.get(tid, {}).get(day, []))


def fmt_slot(day: int, start: int, end: int) -> str:
    return f"{DAY_NAMES[day]} {start:02d}:00–{end:02d}:00"


# ---------------------------------------------------------------- loading
def load_context(conn, week_start: date | None = None,
                 extra_leave_days: dict[int, set[int]] | None = None,
                 extra_leave_reason: str | None = None,
                 new_batches: list[dict] | None = None) -> Context:
    week_start = week_start or config.week_bounds()[0]
    week_end = week_start + timedelta(days=6)

    batches: dict[int, Batch] = {}
    rows = conn.execute("""
        SELECT b.*, c.code AS course_code,
          (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
              AND e.learning_mode='classroom') AS classroom_size,
          (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id=b.id AND e.status='active'
              AND e.learning_mode='online') AS online_size
        FROM batches b JOIN courses c ON c.id=b.course_id
        WHERE b.status='active' ORDER BY b.id""").fetchall()
    for r in rows:
        batches[r["id"]] = Batch(r["id"], r["code"], r["course_code"], r["trainer_id"], r["mode"],
                                 r["time_window"], r["sessions_per_week"], r["session_hours"],
                                 r["classroom_size"], r["online_size"], r["online_seat_limit"])
    for nb in new_batches or []:
        batches[nb["id"]] = Batch(nb["id"], nb["code"], nb["course_code"], nb["trainer_id"],
                                  nb["mode"], nb["time_window"], nb["sessions_per_week"],
                                  nb.get("session_hours", 2), nb.get("classroom_size", 0),
                                  nb.get("online_size", 0), nb.get("online_seat_limit", 0))

    trainers = {r["id"]: {"name": r["name"], "skills": {s.strip() for s in r["skills"].split(",")}}
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
    # online licence concurrency, checked hour by hour
    for day in range(7):
        for hour in range(24):
            live = [i for i, s in enumerate(slots) if s["is_online"] and s["day_of_week"] == day
                    and s["start_hour"] <= hour < s["end_hour"]]
            if len(live) > ctx.online_licences:
                codes = ", ".join(ctx.batches[slots[i]["batch_id"]].code for i in live if slots[i]["batch_id"] in ctx.batches)
                out.append({"type": "online_licences", "slots": live,
                            "message": f"{len(live)} online classes at {DAY_NAMES[day]} {hour:02d}:00 ({codes}) but only {ctx.online_licences} licences"})
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

    def fits(self, b: Batch, tid: int, day: int, start: int, room_id: int | None) -> bool:
        end = start + b.session_hours
        if day in self.batch_days.get(b.id, set()):
            return False
        if not self.ctx.trainer_can(tid, day, start, end):
            return False
        if not self._free(self.trainer_busy.get((tid, day), []), start, end):
            return False
        if b.needs_room:
            if room_id is None or not self._free(self.room_busy.get((room_id, day), []), start, end):
                return False
        if b.needs_online and any(self.online.get((day, h), 0) >= self.ctx.online_licences
                                  for h in range(start, end)):
            return False
        return True

    def place(self, b: Batch, tid: int, day: int, start: int, room_id: int | None) -> None:
        end = start + b.session_hours
        self.trainer_busy.setdefault((tid, day), []).append((start, end))
        if b.needs_room:
            self.room_busy.setdefault((room_id, day), []).append((start, end))
        if b.needs_online:
            for h in range(start, end):
                self.online[(day, h)] = self.online.get((day, h), 0) + 1
        self.batch_days.setdefault(b.id, set()).add(day)

    def remove(self, b: Batch, tid: int, day: int, start: int, room_id: int | None) -> None:
        end = start + b.session_hours
        self.trainer_busy[(tid, day)].remove((start, end))
        if b.needs_room:
            self.room_busy[(room_id, day)].remove((start, end))
        if b.needs_online:
            for h in range(start, end):
                self.online[(day, h)] -= 1
        self.batch_days[b.id].discard(day)


@dataclass
class _Session:
    batch: Batch
    hint: dict | None          # original slot, if any (for minimal change)
    origin_id: int | None      # id of the original slot row
    problem: str | None        # why it has to move


def _rooms_for(ctx: Context, b: Batch) -> list[int | None]:
    if not b.needs_room:
        return [None]
    return [r["id"] for r in ctx.rooms if r["capacity"] >= b.classroom_size]


def _candidates(st: _State, s: _Session, allow_substitute: bool) -> list[tuple]:
    ctx, b, hint = st.ctx, s.batch, s.hint
    win = WINDOWS[b.time_window]
    trainers = [b.trainer_id]
    if allow_substitute:
        trainers += sorted(t for t, info in ctx.trainers.items()
                           if t != b.trainer_id and b.course_code in info["skills"])
    out = []
    for tid in trainers:
        for day in win["days"]:
            for start in win["starts"]:
                if start + b.session_hours > 22:
                    continue
                for rid in _rooms_for(ctx, b):
                    if not st.fits(b, tid, day, start, rid):
                        continue
                    cost = 0.0
                    if tid != b.trainer_id:
                        cost += 10                                   # substitutes are a last resort
                    if hint:
                        same_day, same_start = day == hint["day_of_week"], start == hint["start_hour"]
                        cost += 0 if (same_day and same_start) else 1 if same_day else 2 if same_start else 3
                        cost += abs(day - hint["day_of_week"]) * 0.1
                        if rid != hint.get("room_id"):
                            cost += 0.05
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
    if not any(ctx.trainer_can(b.trainer_id, d, s, s + b.session_hours)
               for d in win["days"] for s in win["starts"]):
        return (f"{ctx.trainer_name(b.trainer_id)} has no availability in the {b.time_window} window this week "
                f"and no free substitute with skill {b.course_code}")
    return (f"every {b.time_window} slot this week clashes with the trainer's other classes, "
            f"room bookings or the {ctx.online_licences} online licences")


def _slot(b: Batch, day: int, start: int, rid: int | None, tid: int) -> dict:
    return {"batch_id": b.id, "trainer_id": tid, "room_id": rid, "day_of_week": day,
            "start_hour": start, "end_hour": start + b.session_hours, "is_online": int(b.needs_online)}


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
        idxs = sorted(by_batch.get(bid, []), key=lambda i: (i in problems, existing[i]["day_of_week"]))
        for i in idxs[b.sessions_per_week:]:
            removed.append(existing[i])
        for i in idxs[:b.sessions_per_week]:
            if i in problems:
                to_place.append(_Session(b, existing[i], existing[i].get("id"), problems[i]))
            else:
                fixed.append(i)
        for _ in range(b.sessions_per_week - min(len(idxs), b.sessions_per_week)):
            to_place.append(_Session(b, None, None, None))

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
                        "reason": f"{b.code} needs only {b.sessions_per_week} classes a week"})

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
        return [{"batch_code": b.code, "type": "added", "from": None, "to": f"{to} ({where})",
                 "reason": f"{b.code} needs {b.sessions_per_week} classes a week; this is a free "
                           f"{b.time_window} slot where trainer, room and online licence are all available"}]
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
    if not same_trainer:
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
        "batch_id": s["batch_id"], "batch_code": b.code,
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
