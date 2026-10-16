"""Create a fresh database filled with a realistic demo institute.

Run:  python -m app.seed

All dates are relative to today (or DEMO_TODAY), so "this week" questions
always have answers. Planted demo cases (use them in your presentation):

  Java batches, missed 3+ classes AND an EMI due this week  -> should be listed
      Rahul Patil   (JAVA-MOR-01)  4 absences, EMI due this week
      Sneha Kadam   (JAVA-MOR-01)  3 absences, EMI due this week
      Manoj Pawar   (JAVA-EVE-02)  3 absences, EMI due this week
  Look-alikes that must NOT be listed
      Pooja Shinde  (JAVA-MOR-01)  EMI due this week, but only 1 absence
      Karan Malhotra(JAVA-EVE-02)  3 absences, but paid in full
  At-risk student
      Aditya Jadhav (JAVA-MOR-01)  6 absences, EMI missed two months running,
                                   2 assignments not submitted
  Timetable problem
      Priya Sharma is double-booked: a JAVA-EVE-02 class was manually moved
      on top of her JAVA-MOR-01 class. "Fix clashes" repairs it.
"""
import random
from datetime import date, datetime, timedelta

from . import config, db, timetable
from .auth import hash_password

rng = random.Random(42)

COURSES = [  # code, name, weeks, fee
    ("JFS", "Java Full Stack", 24, 45000),
    ("PDS", "Python & Data Science", 20, 40000),
    ("RFE", "React Frontend", 12, 25000),
    ("DVO", "DevOps & Cloud", 16, 35000),
    ("SPR", "Spring Boot Microservices", 10, 22000),
]

TRAINERS = [  # name, skills, availability {days: (start, end)}
    ("Priya Sharma", "JFS,SPR", {(0, 1, 2, 3, 4, 5): [(8, 13), (17, 21)]}),
    ("Arjun Mehta", "PDS", {(0, 1, 2, 3, 4): [(10, 18)]}),
    ("Neha Kulkarni", "RFE", {(0, 1, 2, 3, 4, 5): [(8, 14)]}),
    ("Rohit Deshpande", "DVO", {(0, 1, 2, 3, 4): [(15, 21)], (5,): [(8, 14)]}),
    ("Sneha Joshi", "JFS,PDS", {(0, 1, 2, 3, 4, 5): [(8, 21)]}),
    ("Vikram Rao", "RFE,DVO,SPR", {(1, 2, 3, 4, 5, 6): [(9, 21)]}),
]

ROOMS = [("Lab A", 30), ("Lab B", 20), ("Room C", 12), ("Seminar Hall", 40)]

BATCHES = [  # code, course, trainer#, mode, window, per week, online limit, weeks ago, classroom n, online n
    ("JAVA-MOR-01", "JFS", 1, "hybrid", "morning", 3, 15, 7, 14, 6),
    ("JAVA-EVE-02", "JFS", 1, "classroom", "evening", 3, 0, 6, 18, 0),
    ("PY-DS-01", "PDS", 2, "online", "afternoon", 2, 40, 6, 0, 22),
    ("PY-DS-02", "PDS", 2, "hybrid", "afternoon", 2, 10, 5, 8, 6),
    ("REACT-01", "RFE", 3, "classroom", "morning", 3, 0, 6, 16, 0),
    ("REACT-WKD", "RFE", 6, "online", "weekend", 2, 30, 5, 0, 12),
    ("DEVOPS-01", "DVO", 4, "online", "evening", 2, 30, 6, 0, 12),
    ("SPRING-01", "SPR", 6, "hybrid", "evening", 2, 10, 5, 6, 4),
]

TOPICS = {
    "JFS": ["Java basics", "OOP: classes & objects", "Inheritance", "Interfaces", "Collections",
            "Exceptions", "Streams API", "JDBC", "Servlets", "Maven", "HTML & CSS", "JavaScript basics",
            "SQL joins", "Spring core", "Dependency injection", "REST APIs", "Spring Data JPA",
            "Unit testing with JUnit", "Git workflow", "Mini project review", "Hibernate", "Security basics"],
    "PDS": ["Python basics", "Functions", "Lists & dicts", "File handling", "NumPy", "Pandas",
            "Data cleaning", "Matplotlib", "Statistics", "Linear regression", "Classification",
            "Model evaluation", "SQL for analysts", "Feature engineering", "Capstone kickoff"],
    "RFE": ["HTML5", "Modern CSS", "JavaScript ES6", "DOM", "React components", "Props & state",
            "useEffect", "Forms", "React Router", "Context API", "Fetching data", "Deployment"],
    "DVO": ["Linux basics", "Shell scripting", "Git", "Docker", "Docker Compose", "CI with GitHub Actions",
            "AWS EC2", "AWS S3 & IAM", "Kubernetes pods", "Kubernetes services", "Terraform", "Monitoring"],
    "SPR": ["Spring Boot setup", "@Autowired & beans", "REST controllers", "Validation", "JPA",
            "Microservices intro", "Service discovery", "API gateway", "Resilience", "Docker for services"],
}

FIRST = ["Aarav", "Vivaan", "Aditi", "Ananya", "Ishaan", "Diya", "Kabir", "Meera", "Rohan", "Saanvi",
         "Arjun", "Kavya", "Yash", "Riya", "Omkar", "Shruti", "Tanvi", "Siddharth", "Nikhil", "Prachi",
         "Harsh", "Gauri", "Varun", "Neha", "Akash", "Isha", "Tejas", "Mrunal", "Pranav", "Sakshi",
         "Ritesh", "Swara", "Atharva", "Janhavi", "Sahil", "Payal", "Kunal", "Rutuja", "Abhishek", "Vaishnavi"]
LAST = ["Sharma", "Patil", "Deshmukh", "Joshi", "Kulkarni", "Gupta", "Iyer", "Reddy", "Nair", "Chavan",
        "More", "Gaikwad", "Bhosale", "Mehta", "Shah", "Pandey", "Verma", "Rao", "Sawant", "Pawar",
        "Kale", "Shinde", "Jadhav", "Desai", "Menon"]
CITIES = ["Pune", "Pune", "Pune", "Pimpri-Chinchwad", "Mumbai", "Nashik", "Satara", "Kolhapur"]

# name, batch code, learning mode, absences, plant
PLANTED = [
    ("Rahul Patil", "JAVA-MOR-01", "classroom", 4, "emi_due_this_week"),
    ("Sneha Kadam", "JAVA-MOR-01", "online", 3, "emi_due_this_week"),
    ("Aditya Jadhav", "JAVA-MOR-01", "classroom", 6, "emi_overdue_twice"),
    ("Pooja Shinde", "JAVA-MOR-01", "classroom", 1, "emi_due_this_week"),
    ("Karan Malhotra", "JAVA-EVE-02", "classroom", 3, "paid_full"),
    ("Manoj Pawar", "JAVA-EVE-02", "classroom", 3, "emi_due_this_week"),
]


def _phone() -> str:
    return "+91" + rng.choice("6789") + "".join(rng.choice("0123456789") for _ in range(9))


def _ref(method: str) -> str:
    if method == "UPI":
        return "UPI/" + "".join(rng.choice("0123456789") for _ in range(12))
    if method == "bank_transfer":
        return "UTR" + "".join(rng.choice("0123456789") for _ in range(14))
    return ""


def seed() -> dict:
    today = config.today()
    week_start, week_end = config.week_bounds(today)
    db.init_db(reset=True)
    conn = db.get_conn()
    cur = conn.cursor()

    # ---- courses, trainers, rooms
    course_id = {}
    for code, name, weeks, fee in COURSES:
        cur.execute("INSERT INTO courses (code, name, duration_weeks, total_fee) VALUES (?,?,?,?)",
                    (code, name, weeks, fee))
        course_id[code] = cur.lastrowid
    for i, (name, skills, avail) in enumerate(TRAINERS, start=1):
        email = name.lower().replace(" ", ".") + "@techsummit.in"
        cur.execute("INSERT INTO trainers (id, name, email, phone, skills) VALUES (?,?,?,?,?)",
                    (i, name, email, _phone(), skills))
        for days, windows in avail.items():
            for d in days:
                for s, e in windows:
                    cur.execute("INSERT INTO trainer_availability (trainer_id, day_of_week, start_hour, end_hour) "
                                "VALUES (?,?,?,?)", (i, d, s, e))
    for name, cap in ROOMS:
        cur.execute("INSERT INTO rooms (name, capacity) VALUES (?,?)", (name, cap))

    # ---- batches
    batch = {}
    for code, course, tid, mode, window, spw, olim, weeks_ago, n_class, n_online in BATCHES:
        start = week_start - timedelta(weeks=weeks_ago)
        weeks = next(c[2] for c in COURSES if c[0] == course)
        cur.execute("INSERT INTO batches (code, course_id, trainer_id, mode, time_window, sessions_per_week, "
                    "session_hours, online_seat_limit, start_date, end_date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (code, course_id[course], tid, mode, window, spw, 2, olim, start.isoformat(),
                     (start + timedelta(weeks=weeks)).isoformat(), "active"))
        batch[code] = {"id": cur.lastrowid, "course": course, "start": start, "mode": mode,
                       "n_class": n_class, "n_online": n_online}

    # ---- students and enrollments
    used_names = {p[0] for p in PLANTED}
    students = []      # dicts: id, name, batch, mode, absences, plant

    def new_student(name, bcode, lmode, absences, plant=None):
        b = batch[bcode]
        enrolled = b["start"] - timedelta(days=rng.randint(3, 12))
        first = name.split()[0].lower()
        cur.execute("INSERT INTO students (name, phone, email, city, guardian_phone, joined_on) VALUES (?,?,?,?,?,?)",
                    (name, _phone(), f"{first}.{name.split()[-1].lower()}{rng.randint(1, 99)}@gmail.com",
                     rng.choice(CITIES), _phone(), enrolled.isoformat()))
        sid = cur.lastrowid
        cur.execute("INSERT INTO enrollments (student_id, batch_id, enrolled_on, learning_mode, status) "
                    "VALUES (?,?,?,?, 'active')", (sid, b["id"], enrolled.isoformat(), lmode))
        rec = {"id": sid, "name": name, "batch": bcode, "mode": lmode, "absences": absences,
               "plant": plant, "enrollment_id": cur.lastrowid, "enrolled": enrolled}
        students.append(rec)
        return rec

    for name, bcode, lmode, absences, plant in PLANTED:
        new_student(name, bcode, lmode, absences, plant)

    def random_name():
        while True:
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in used_names:
                used_names.add(n)
                return n

    weak_slots = 4  # a few struggling students outside the Java batches
    for code, b in batch.items():
        have_c = sum(1 for s in students if s["batch"] == code and s["mode"] == "classroom")
        have_o = sum(1 for s in students if s["batch"] == code and s["mode"] == "online")
        for lmode, need in (("classroom", b["n_class"] - have_c), ("online", b["n_online"] - have_o)):
            for _ in range(max(0, need)):
                absences = rng.choice([0, 0, 0, 1, 1, 2])
                plant = None
                if not code.startswith("JAVA") and weak_slots and rng.random() < 0.12:
                    absences, plant, weak_slots = rng.randint(3, 6), "weak", weak_slots - 1
                new_student(random_name(), code, lmode, absences, plant)

    # A few Java students also take the Spring Boot batch (shows multi-enrollment)
    for s in [s for s in students if s["batch"] == "JAVA-EVE-02" and not s["plant"]][:3]:
        b = batch["SPRING-01"]
        cur.execute("INSERT INTO enrollments (student_id, batch_id, enrolled_on, learning_mode, status) "
                    "VALUES (?,?,?,?, 'active')", (s["id"], b["id"], b["start"].isoformat(), "online"))
        students.append({**s, "batch": "SPRING-01", "mode": "online", "absences": rng.choice([0, 1]),
                         "plant": None, "enrollment_id": cur.lastrowid, "fee_done": True})
    # one dropped student
    dropped = next(s for s in students if s["batch"] == "PY-DS-01" and not s["plant"])
    cur.execute("UPDATE enrollments SET status='dropped' WHERE id=?", (dropped["enrollment_id"],))
    dropped["dropped"] = True
    conn.commit()

    # ---- weekly pattern from the planner (clash-free)
    ctx = timetable.load_context(conn, week_start)
    result = timetable.plan(ctx, [])
    assert not result["unscheduled"], result["unscheduled"]
    pattern = result["slots"]

    # ---- class sessions (history + rest of this week)
    sessions_by_batch: dict[str, list[dict]] = {}
    for code, b in batch.items():
        slots = [s for s in pattern if s["batch_code"] == code]
        topics = TOPICS[b["course"]]
        n = 0
        d = b["start"]
        while d <= week_end:
            for s in slots:
                if d.weekday() == s["day_of_week"]:
                    status = "held" if d < today else "scheduled"
                    cur.execute("INSERT INTO class_sessions (batch_id, session_date, start_time, end_time, topic, status) "
                                "VALUES (?,?,?,?,?,?)", (b["id"], d.isoformat(), f"{s['start_hour']:02d}:00",
                                                         f"{s['end_hour']:02d}:00", topics[n % len(topics)], status))
                    n += 1
                    if status == "held":
                        sessions_by_batch.setdefault(code, []).append(
                            {"id": cur.lastrowid, "date": d, "start": s["start_hour"]})
            d += timedelta(days=1)

    # ---- attendance (hybrid: classroom check-ins + online join logs) and recordings
    for code, held in sessions_by_batch.items():
        for sess in held:
            if batch[code]["mode"] in ("online", "hybrid"):
                cur.execute("INSERT INTO recordings (session_id, url, duration_min, views) VALUES (?,?,?,?)",
                            (sess["id"], f"https://recordings.techsummit.in/{code.lower()}/{sess['date'].isoformat()}",
                             rng.randint(100, 125), rng.randint(2, 25)))
    for s in students:
        held = [x for x in sessions_by_batch.get(s["batch"], []) if x["date"] >= s["enrolled"]]
        if s.get("dropped"):
            held = held[: max(3, len(held) // 3)]
        k = min(s["absences"], len(held))
        if s["plant"] in ("emi_due_this_week", "emi_overdue_twice", "paid_full", "weak"):
            recent = held[-9:]                     # planted problems are recent
            absent_ids = {x["id"] for x in rng.sample(recent, min(k, len(recent)))}
        else:
            absent_ids = {x["id"] for x in rng.sample(held, k)}
        for x in held:
            if x["id"] in absent_ids:
                cur.execute("INSERT INTO attendance (session_id, student_id, status) VALUES (?,?, 'absent')",
                            (x["id"], s["id"]))
                continue
            late = rng.random() < 0.08
            if s["mode"] == "classroom":
                minute = rng.randint(10, 25) if late else rng.randint(0, 5)
                hour = x["start"] if minute < 60 else x["start"] + 1
                cur.execute("INSERT INTO attendance (session_id, student_id, status, mode, check_in_time) "
                            "VALUES (?,?,?,?,?)", (x["id"], s["id"], "late" if late else "present",
                                                   "classroom", f"{hour:02d}:{minute:02d}"))
            else:
                mins = rng.randint(60, 95) if late else rng.randint(105, 120)
                cur.execute("INSERT INTO attendance (session_id, student_id, status, mode, online_minutes) "
                            "VALUES (?,?,?,?,?)", (x["id"], s["id"], "late" if late else "present", "online", mins))

    # ---- assignments and submissions
    for code, b in batch.items():
        for wk, title in ((2, "Assignment 1"), (4, "Assignment 2"), (6, "Mini project")):
            due = b["start"] + timedelta(weeks=wk, days=4)
            if due >= today:
                continue
            cur.execute("INSERT INTO assignments (batch_id, title, due_date) VALUES (?,?,?)",
                        (b["id"], f"{title}: {TOPICS[b['course']][wk * 2 % len(TOPICS[b['course']])]}", due.isoformat()))
            aid = cur.lastrowid
            for s in [s for s in students if s["batch"] == code and not s.get("dropped")]:
                miss = (s["plant"] == "emi_overdue_twice" and wk >= 4) or \
                       (s["plant"] == "weak" and rng.random() < 0.6) or rng.random() < 0.05
                if miss:
                    continue
                low = s["plant"] in ("weak", "emi_overdue_twice")
                cur.execute("INSERT INTO submissions (assignment_id, student_id, submitted_on, score) VALUES (?,?,?,?)",
                            (aid, s["id"], (due - timedelta(days=rng.randint(0, 3))).isoformat(),
                             rng.randint(30, 55) if low else rng.randint(58, 97)))

    # ---- fees and EMIs
    due1 = min(today + timedelta(days=1), week_end)
    due2 = min(today + timedelta(days=2), week_end)
    fee_of = {c[0]: c[3] for c in COURSES}
    partial_done = False
    for s in students:
        if s.get("fee_done"):
            continue
        total = fee_of[batch[s["batch"]]["course"]]
        emi = s["plant"] in ("emi_due_this_week", "emi_overdue_twice") or \
            (s["plant"] != "paid_full" and rng.random() < 0.6)
        n = rng.choice([3, 4, 6]) if emi else 1
        if s["plant"] == "emi_overdue_twice":
            n = 4
        discount = rng.choice([0, 0, 0, 2000, 5000]) if not emi else 0
        payable = total - discount
        cur.execute("INSERT INTO fee_plans (enrollment_id, plan_type, total_amount, discount, instalments_count) "
                    "VALUES (?,?,?,?,?)", (s["enrollment_id"], "emi" if emi else "full", payable, discount, n))
        plan_id = cur.lastrowid
        amounts = [payable // n] * n
        amounts[-1] += payable - sum(amounts)
        dues = [s["enrolled"] + timedelta(days=30 * k) for k in range(n)]
        if s["plant"] == "emi_overdue_twice":
            dues = [s["enrolled"], today - timedelta(days=35), today - timedelta(days=5), today + timedelta(days=25)]
        if s["plant"] == "emi_due_this_week":
            nxt = next((k for k, d in enumerate(dues) if d >= today), None)
            if nxt is None or nxt == 0:
                nxt = 1
            dues[nxt] = due1 if s["name"] in ("Rahul Patil", "Pooja Shinde") else due2
        overdue_random = s["plant"] == "weak" or (not s["plant"] and emi and rng.random() < 0.07)
        for k, (amt, due) in enumerate(zip(amounts, dues), start=1):
            if due < today:
                missed = (s["plant"] == "emi_overdue_twice" and k in (2, 3)) or \
                         (overdue_random and k == max(i for i, d in enumerate(dues, 1) if d < today) and k > 1)
                if missed:
                    status, paid, paid_on = "overdue", 0, None
                elif not partial_done and emi and k > 1 and rng.random() < 0.05:
                    status, paid, paid_on, partial_done = "partial", amt // 2, due.isoformat(), True
                else:
                    status, paid = "paid", amt
                    paid_on = (due + timedelta(days=rng.randint(-3, 2))).isoformat()
            else:
                status, paid, paid_on = "due", 0, None
            cur.execute("INSERT INTO instalments (fee_plan_id, instalment_no, due_date, amount, paid_amount, status, paid_on) "
                        "VALUES (?,?,?,?,?,?,?)", (plan_id, k, due.isoformat(), amt, paid, status, paid_on))
            if paid:
                method = rng.choice(["UPI", "UPI", "UPI", "bank_transfer", "cash", "card"])
                cur.execute("INSERT INTO payments (instalment_id, student_id, amount, paid_on, method, reference) "
                            "VALUES (?,?,?,?,?,?)", (cur.lastrowid, s["id"], paid, paid_on, method, _ref(method)))

    # ---- this week's timetable, with one manual mistake planted
    slots = [{k: s[k] for k in ("batch_id", "trainer_id", "room_id", "day_of_week",
                                "start_hour", "end_hour", "is_online")} for s in pattern]
    mor = [s for s in slots if s["batch_id"] == batch["JAVA-MOR-01"]["id"]]
    eve = [s for s in slots if s["batch_id"] == batch["JAVA-EVE-02"]["id"]]
    eve_days = {s["day_of_week"] for s in eve}
    clash_note = None
    for target in mor:
        movable = [s for s in eve if target["day_of_week"] not in eve_days - {s["day_of_week"]}]
        if movable:
            m = movable[0]
            m["day_of_week"], m["start_hour"], m["end_hour"] = (
                target["day_of_week"], target["start_hour"], target["end_hour"])
            m["room_id"] = next(r for r in (1, 2, 4) if r != target["room_id"])
            clash_note = (f"JAVA-EVE-02 moved onto {timetable.fmt_slot(target['day_of_week'], target['start_hour'], target['end_hour'])}, "
                          f"same time as Priya's JAVA-MOR-01")
            break
    timetable.save_slots(conn, week_start, slots)

    # ---- users
    users = [
        ("owner", "owner123", "owner", "Anil Bhosale", None),
        ("counselor", "counsel123", "counselor", "Kavya Nair", None),
        ("accounts", "accounts123", "accounts", "Ramesh Iyer", None),
        ("priya", "trainer123", "trainer", "Priya Sharma", 1),
        ("arjun", "trainer123", "trainer", "Arjun Mehta", 2),
        ("neha", "trainer123", "trainer", "Neha Kulkarni", 3),
        ("rohit", "trainer123", "trainer", "Rohit Deshpande", 4),
        ("sneha", "trainer123", "trainer", "Sneha Joshi", 5),
        ("vikram", "trainer123", "trainer", "Vikram Rao", 6),
    ]
    for u, p, role, name, tid in users:
        cur.execute("INSERT INTO users (username, password_hash, role, display_name, trainer_id) VALUES (?,?,?,?,?)",
                    (u, hash_password(p), role, name, tid))
    cur.execute("INSERT INTO audit_log (ts, event_type, outcome, detail_json) VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), "seed", "ok",
                 f'{{"today": "{today.isoformat()}"}}'))
    conn.commit()

    summary = {
        "today": today.isoformat(),
        "week": f"{week_start} to {week_end}",
        "students": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "enrollments": conn.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0],
        "batches": len(batch),
        "class_sessions": conn.execute("SELECT COUNT(*) FROM class_sessions").fetchone()[0],
        "attendance_rows": conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0],
        "instalments": conn.execute("SELECT COUNT(*) FROM instalments").fetchone()[0],
        "payments": conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
        "timetable_slots": len(slots),
        "planted_clash": clash_note,
    }
    conn.close()
    return summary


if __name__ == "__main__":
    s = seed()
    print("Database created at", config.DB_PATH)
    for k, v in s.items():
        print(f"  {k:16} {v}")
    print("\nLogins: owner/owner123  counselor/counsel123  accounts/accounts123  "
          "priya/trainer123 (also arjun, neha, rohit, sneha, vikram)")
