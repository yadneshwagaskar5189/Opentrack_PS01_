"""Load the institute's own CSV files into the database. This REPLACES all data
(students, fees, timetable, approvals and the audit log).

Run from the project folder (the one that contains app/ and seed_data/):

    python -m app.import_csv                  reads ./seed_data
    python -m app.import_csv D:\\my\\csvs       reads another folder

Files (one header row each; extra columns are ignored, optional ones may be missing):
  required  courses, rooms, trainers, batches, students, enrollments, users
  optional  trainer_availability, trainer_leaves, sessions, attendance, recordings,
            assignments, submissions, fee_plans, emis, payments, bank_statement, batch_requests

IDs such as S001, B01, T02 or EMI0001 become the numbers 1, 1, 2 and 1 inside the database;
batch codes (B01), course codes (C01) and request codes (NB01) are kept as they are.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import audit, config, db, timetable
from .auth import hash_password

REQUIRED = ["courses", "rooms", "trainers", "batches", "students", "enrollments", "users"]
DAY = {d.lower(): i for i, d in enumerate(timetable.DAY_NAMES)}
METHODS = {"cash": "cash", "upi": "UPI", "bank_transfer": "bank_transfer", "neft": "bank_transfer",
           "imps": "bank_transfer", "rtgs": "bank_transfer", "bank": "bank_transfer", "card": "card",
           "cheque": "bank_transfer"}


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------- helpers
def find_folder(path: str | Path | None = None, required: bool = True) -> Path | None:
    """The folder holding students.csv. Also accepts the parent of that folder (seed_data/seed_data)."""
    folder = Path(path or config.SEED_DIR)
    if (folder / "students.csv").exists():
        return folder
    if folder.is_dir():
        inner = [p for p in folder.iterdir() if p.is_dir() and (p / "students.csv").exists()]
        if len(inner) == 1:
            return inner[0]
    if required:
        raise ImportError_(f"No students.csv found in {folder.resolve()}. Put the CSV files in a folder called "
                           f"seed_data next to the app folder, or pass the folder: python -m app.import_csv <folder>")
    return None


def read(folder: Path, name: str, required: bool = False) -> list[dict]:
    f = folder / f"{name}.csv"
    if not f.exists():
        if required:
            raise ImportError_(f"Missing {f.name} in {folder}")
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(fh) if any((v or "").strip() for v in row.values())]


def num(ref: str) -> int:
    """'S001' -> 1, 'EMI0012' -> 12, 'R1' -> 1"""
    digits = re.sub(r"\D", "", ref or "")
    if not digits:
        raise ImportError_(f"Can't read an id from {ref!r}")
    return int(digits)


def opt_num(ref: str) -> int | None:
    return num(ref) if ref else None


def money(v: str) -> int:
    v = (v or "0").replace(",", "").replace("₹", "").strip()
    return int(round(float(v))) if v else 0


def phone(v: str) -> str | None:
    digits = re.sub(r"\D", "", v or "")
    if not digits:
        return None
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return "+" + digits if not v.startswith("+") else v


def days_of(v: str) -> list[str]:
    out = []
    for part in re.split(r"[|,/; ]+", v or ""):
        p = part.strip()[:3].lower()
        if p in DAY:
            out.append(timetable.DAY_NAMES[DAY[p]])
    return out


def time_part(v: str) -> str | None:
    """'2026-09-21 09:08' -> '09:08'"""
    m = re.search(r"(\d{1,2}):(\d{2})", v or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def minutes_between(a: str, b: str) -> int | None:
    try:
        fmt = "%Y-%m-%d %H:%M"
        return int((datetime.strptime(b[:16], fmt) - datetime.strptime(a[:16], fmt)).total_seconds() // 60)
    except (ValueError, TypeError):
        return None


def pick(row: dict, *names: str, default: str = "") -> str:
    for n in names:
        if row.get(n):
            return row[n]
    return default


# ---------------------------------------------------------------- import
def import_folder(path: str | Path | None = None, institute_name: str | None = None) -> dict:
    folder = find_folder(path)
    data = {name: read(folder, name, required=name in REQUIRED) for name in REQUIRED}
    for name in ["trainer_availability", "trainer_leaves", "sessions", "attendance", "recordings", "assignments",
                 "submissions", "fee_plans", "emis", "payments", "bank_statement", "batch_requests"]:
        data[name] = read(folder, name)
    today = config.today()

    db.init_db(reset=True)
    conn = db.get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")          # rows arrive table by table; checked at the end
    cur = conn.cursor()
    try:
        _load(cur, data, today)
        name = institute_name or os.getenv("INSTITUTE_NAME") or _guess_name(data["users"])
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('institute_name', ?)", (name,))
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('data_source', ?)", (str(folder.resolve()),))
        conn.commit()
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            t, rowid, parent, _ = problems[0]
            raise ImportError_(f"{len(problems)} row(s) point at something that doesn't exist, "
                               f"e.g. {t} row {rowid} -> missing {parent}. Check the ids in the CSV files.")
        week = config.plan_week()[0]
        slots = timetable.ensure_week(conn, week)
        cur.execute("INSERT INTO audit_log (ts, event_type, outcome, detail_json) VALUES (?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), "seed", "ok",
                     f'{{"source": "csv", "today": "{today.isoformat()}"}}'))
        audit.seal(conn)
        conn.commit()
        ctx = timetable.load_context(conn, week)
        problems_now = [v["message"] for v in timetable.find_violations(ctx, timetable.load_slots(conn, week))]
        count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
        return {
            "source": str(folder.resolve()),
            "institute": name,
            "today": today.isoformat(),
            "timetable_week": f"{week} to {week + timedelta(days=6)}",
            **{t: count(t) for t in ["users", "courses", "trainers", "rooms", "batches", "students", "enrollments",
                                     "class_sessions", "attendance", "recordings", "assignments", "submissions",
                                     "fee_plans", "instalments", "payments", "bank_transactions",
                                     "batch_requests", "trainer_leaves"]},
            "timetable_slots": slots,
            "timetable_problems": problems_now,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _guess_name(users: list[dict]) -> str:
    for u in users:
        m = re.search(r"@([a-z0-9-]+)\.", u.get("email", "").lower())
        if m:
            return m.group(1).replace("-", " ").title() + " Institute"
    return "Our Institute"


def _load(cur, d: dict, today: date) -> None:
    ins = lambda table, row: cur.execute(  # noqa: E731
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))

    for r in d["courses"]:
        ins("courses", {"id": num(r["course_id"]), "code": r["course_id"], "name": r["name"],
                        "duration_weeks": int(pick(r, "duration_weeks", default="12")),
                        "total_fee": money(r.get("total_fee")), "emi_count": int(r.get("num_emis") or 1)})
    for r in d["rooms"]:
        ins("rooms", {"id": num(r["room_id"]), "name": r["name"], "capacity": int(r["capacity"]),
                      "has_projector": 0 if r.get("has_projector", "Y").upper().startswith("N") else 1})
    for r in d["trainers"]:
        skills = ",".join(s.strip() for s in re.split(r"[|,;]", r.get("skills", "")) if s.strip())
        ins("trainers", {"id": num(r["trainer_id"]), "name": r["name"], "email": r.get("email") or None,
                         "phone": phone(r.get("phone")), "skills": skills})
    for r in d["trainer_availability"]:
        for day in days_of(r["day"]):
            ins("trainer_availability", {"trainer_id": num(r["trainer_id"]), "day_of_week": DAY[day.lower()],
                                         "start_hour": timetable.to_hours(r["start_time"]),
                                         "end_hour": timetable.to_hours(r["end_time"])})
    for r in d["trainer_leaves"]:
        if r.get("status") and r["status"].upper() not in ("APPROVED", "CONFIRMED"):
            continue
        start = date.fromisoformat(r["start_date"])
        end = date.fromisoformat(r.get("end_date") or r["start_date"])
        while start <= end:
            ins("trainer_leaves", {"trainer_id": num(r["trainer_id"]), "leave_date": start.isoformat(),
                                   "reason": r.get("reason") or "leave"})
            start += timedelta(days=1)

    for r in d["batches"]:
        days = days_of(r.get("days"))
        start_h = timetable.to_hours(r["start_time"]) if r.get("start_time") else 10
        end_h = timetable.to_hours(r["end_time"]) if r.get("end_time") else start_h + 2
        limit = int(r.get("online_seat_limit") or 0)
        mode = (r.get("mode") or ("hybrid" if limit > 0 else "classroom")).lower()
        status = (r.get("status") or "active").lower()
        ins("batches", {
            "id": num(r["batch_id"]), "code": r["batch_id"], "name": r.get("name") or None,
            "course_id": num(r["course_id"]), "trainer_id": num(r["trainer_id"]),
            "mode": mode if mode in ("classroom", "online", "hybrid") else "hybrid",
            "time_window": r.get("time_window") or timetable.window_for([DAY[x.lower()] for x in days], start_h),
            "sessions_per_week": len(days) or int(r.get("sessions_per_week") or 2),
            "session_hours": timetable._num(end_h - start_h), "online_seat_limit": limit,
            "days": ",".join(days) or None, "start_time": r.get("start_time") or None,
            "end_time": r.get("end_time") or None, "room_id": opt_num(r.get("room_id")),
            "start_date": r["start_date"], "end_date": r["end_date"],
            "status": status if status in ("active", "upcoming", "completed") else "active"})

    for r in d["students"]:
        ins("students", {"id": num(r["student_id"]), "name": r["name"], "phone": phone(r.get("phone")) or "",
                         "email": r.get("email") or None, "city": r.get("city") or None,
                         "parent_name": r.get("parent_name") or None,
                         "guardian_phone": phone(pick(r, "parent_phone", "guardian_phone")),
                         "joined_on": pick(r, "joined_on", "enrolled_on", default=today.isoformat())})
    student_of = {}
    for r in d["enrollments"]:
        eid = num(r["enrollment_id"])
        student_of[eid] = num(r["student_id"])
        lm = pick(r, "attendance_mode_pref", "learning_mode", default="classroom").lower()
        st = (r.get("status") or "active").lower()
        ins("enrollments", {"id": eid, "student_id": num(r["student_id"]), "batch_id": num(r["batch_id"]),
                            "enrolled_on": r.get("enrolled_on") or today.isoformat(),
                            "learning_mode": "online" if lm == "online" else "classroom",
                            "status": st if st in ("active", "completed", "dropped") else "active"})

    for r in d["sessions"]:
        ins("class_sessions", {"id": num(r["session_id"]), "batch_id": num(r["batch_id"]),
                               "session_date": r["session_date"], "start_time": r["start_time"],
                               "end_time": r["end_time"], "topic": r.get("topic") or None,
                               "status": "held" if r["session_date"] < today.isoformat() else "scheduled"})
    for r in d["attendance"]:
        status = r["status"].lower()
        status = status if status in ("present", "late", "absent") else "present"
        mode = (r.get("mode") or "").lower()
        mode = mode if mode in ("classroom", "online") and status != "absent" else None
        ins("attendance", {"id": num(r["attendance_id"]), "session_id": num(r["session_id"]),
                           "student_id": num(r["student_id"]), "status": status, "mode": mode,
                           "check_in_time": time_part(r.get("checkin_time")) if mode == "classroom" else None,
                           "online_minutes": minutes_between(r.get("join_time"), r.get("leave_time"))
                           if mode == "online" else None})
    for r in d["recordings"]:
        ins("recordings", {"id": num(r["recording_id"]), "session_id": num(r["session_id"]), "url": r["url"],
                           "duration_min": int(r.get("duration_min") or 0), "views": int(r.get("views") or 0)})
    for r in d["assignments"]:
        ins("assignments", {"id": num(r["assignment_id"]), "batch_id": num(r["batch_id"]),
                            "title": r["title"], "due_date": r["due_date"]})
    for r in d["submissions"]:
        st = (r.get("status") or "submitted").lower()
        if st == "missing" or not r.get("submitted_on"):
            continue                                # no row = not submitted
        ins("submissions", {"id": num(r["submission_id"]), "assignment_id": num(r["assignment_id"]),
                            "student_id": num(r["student_id"]), "submitted_on": r["submitted_on"],
                            "score": int(float(r["score"])) if r.get("score") else None,
                            "status": "late" if st == "late" else "submitted"})

    for r in d["fee_plans"]:
        n = int(r.get("num_emis") or 1)
        ins("fee_plans", {"id": num(r["enrollment_id"]), "enrollment_id": num(r["enrollment_id"]),
                          "plan_type": "emi" if n > 1 else "full",
                          "total_amount": money(pick(r, "net_fee", "total_fee")), "discount": money(r.get("discount")),
                          "instalments_count": n})
    for r in d["emis"]:
        st = r["status"].lower()
        if st not in ("paid", "partial", "overdue"):
            st = "due" if r["due_date"] >= today.isoformat() else "overdue"
        ins("instalments", {"id": num(r["emi_id"]), "fee_plan_id": num(r["enrollment_id"]),
                            "instalment_no": int(r["emi_no"]), "due_date": r["due_date"],
                            "amount": money(r["amount"]), "paid_amount": money(r.get("paid_amount")),
                            "status": st, "paid_on": r.get("paid_on") or None})
    for r in d["payments"]:
        eid = num(r["enrollment_id"])
        method = METHODS.get(r.get("method", "").lower().replace(" ", "_"), "UPI")
        ins("payments", {"id": num(r["payment_id"]), "instalment_id": opt_num(r.get("emi_id")),
                         "student_id": student_of.get(eid) or num(r.get("student_id", "")), "amount": money(r["amount"]),
                         "paid_on": r["paid_on"], "method": method, "reference": r.get("reference") or None})
    for r in d["bank_statement"]:
        ins("bank_transactions", {"id": num(r["txn_id"]), "txn_ref": r["txn_id"], "txn_date": r["txn_date"],
                                  "description": r.get("description", ""), "credit": money(r.get("credit")),
                                  "utr": r.get("utr") or None,
                                  "balance": money(r["balance"]) if r.get("balance") else None})
    for r in d["batch_requests"]:
        window = re.findall(r"\d{1,2}:\d{2}", r.get("preferred_window", ""))
        ins("batch_requests", {"id": num(r["request_id"]), "code": r["request_id"], "course_name": r["course_name"],
                               "expected_students": int(r.get("expected_students") or 0),
                               "online_seats_needed": int(r.get("online_seats_needed") or 0),
                               "preferred_days": ",".join(days_of(r.get("preferred_days"))) or None,
                               "preferred_start": window[0] if window else None,
                               "preferred_end": window[1] if len(window) > 1 else None,
                               "mode": (r.get("mode") or "hybrid").lower(),
                               "required_skill": r.get("required_skill") or None, "note": r.get("note") or None})

    for r in d["users"]:
        role = r["role"].lower()
        if role not in ("owner", "counselor", "trainer", "accounts"):
            raise ImportError_(f"users.csv: unknown role {r['role']!r} for {r.get('email')}")
        ins("users", {"id": num(r["user_id"]), "username": pick(r, "email", "username").lower(),
                      "password_hash": hash_password(pick(r, "demo_password", "password", default="demo123")),
                      "role": role, "display_name": r["name"],
                      "trainer_id": opt_num(r.get("trainer_id")) if role == "trainer" else None})


# ---------------------------------------------------------------- start-up / reset
def build_demo_database() -> dict:
    """Used on first start and by 'Reset demo data': the CSV folder if there is one, else sample data."""
    if find_folder(required=False):
        return import_folder()
    from . import seed
    return seed.seed()


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    name = next((a.split("=", 1)[1] for a in argv if a.startswith("--name=")), None)
    try:
        s = import_folder(args[0] if args else None, name)
    except ImportError_ as e:
        print("Import failed:", e)
        return 1
    print(f"Loaded {s['institute']} from {s['source']}")
    print(f"Database: {config.DB_PATH}")
    print(f"Today is {s['today']} (set DEMO_TODAY in .env to freeze it); timetable week {s['timetable_week']}")
    for k in ["students", "enrollments", "batches", "trainers", "class_sessions", "attendance", "instalments",
              "payments", "bank_transactions", "timetable_slots"]:
        print(f"  {k:18} {s[k]}")
    if s["timetable_problems"]:
        print("Timetable problems for the copilot to fix:")
        for p in s["timetable_problems"]:
            print("  -", p)
    print("\nLog in with the emails in users.csv, e.g. owner@... and the demo password (demo123).")
    print("Restart uvicorn if it is running, then refresh the browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
