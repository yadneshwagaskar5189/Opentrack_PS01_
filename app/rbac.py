"""Role-based access: which database objects and which actions each role may use.

This is the single source of truth. The copilot's prompt is built from it,
and the SQLite authorizer enforces it on every query (see sql_guard.py),
so a clever prompt can never widen what a role can read.
"""

# What each view/table is, shown to the LLM so it can write good SQL.
OBJECT_DOCS = {
    "v_courses_public": "Courses offered (no fees).",
    "v_courses_with_fee": "Courses with total fee in rupees.",
    "v_rooms": "Classrooms and seat capacity.",
    "v_trainers_public": "Trainers and their skills (comma list of course codes).",
    "v_batches": "Batches: course, trainer, mode (classroom/online/hybrid), time_window, dates, status.",
    "v_timetable": "This week's timetable. day_of_week 0=Mon..6=Sun, hours on 24h clock, is_online=1 uses an online licence.",
    "v_students": "Students with phone (+91...), email, city, guardian_phone.",
    "v_enrollments": "Which student is in which batch. status active/completed/dropped; learning_mode classroom/online.",
    "v_class_sessions": "Classes held per batch with date and topic. status held/cancelled/scheduled.",
    "v_attendance": "One row per student per class: status present/late/absent, mode classroom/online, check-in time, online minutes. 'Missed a class' means status='absent'.",
    "v_assignments": "Assignments per batch with due date.",
    "v_submissions": "Assignment submissions with score out of 100. A missing row means not submitted.",
    "v_recordings": "Class recordings with URL, duration and view count.",
    "v_instalments": "Fee instalments (EMIs). status paid/due/overdue/partial; balance = amount - paid_amount; student_phone for reminders.",
    "v_fee_plans": "Fee plan per enrollment: full or emi, total, discount, number of instalments.",
    "v_payments": "Payments received: amount, date, method (UPI/bank_transfer/cash/card), reference (UPI ref/UTR).",
    "trainers": "Trainers with email and phone.",
    "trainer_availability": "Weekly availability per trainer: day_of_week 0=Mon..6=Sun, start_hour, end_hour.",
    "trainer_leaves": "Trainer leave dates.",
    "certificates": "Completion certificates issued per enrollment.",
    "messages_outbox": "Messages sent after human approval.",
    "audit_log": "Every copilot question, query, refusal and approved action.",
    "t_my_batches": "Your batches only.",
    "t_my_students": "Students in your batches (no phone numbers, no fee data).",
    "t_my_sessions": "Classes of your batches.",
    "t_my_attendance": "Attendance in your batches. 'Missed a class' means status='absent'.",
    "t_my_assignments": "Assignments of your batches.",
    "t_my_submissions": "Submissions for your assignments, score out of 100.",
    "t_my_recordings": "Recordings of your classes.",
    "t_my_timetable": "Your timetable this week. day_of_week 0=Mon..6=Sun.",
}

ROLES = {
    "owner": {
        "label": "Owner",
        "objects": [
            "v_students", "v_enrollments", "v_batches", "v_courses_with_fee", "v_trainers_public",
            "v_rooms", "v_class_sessions", "v_attendance", "v_assignments", "v_submissions",
            "v_recordings", "v_instalments", "v_fee_plans", "v_payments", "v_timetable",
            "trainers", "trainer_availability", "trainer_leaves", "certificates",
            "messages_outbox", "audit_log",
        ],
        "actions": ["send_whatsapp", "mark_instalment_paid", "apply_timetable"],
        "can_see_fees": True,
    },
    "counselor": {
        "label": "Counselor",
        "objects": [
            "v_students", "v_enrollments", "v_batches", "v_courses_with_fee", "v_trainers_public",
            "v_class_sessions", "v_attendance", "v_assignments", "v_submissions",
            "v_recordings", "v_instalments", "v_timetable",
        ],
        "actions": ["send_whatsapp"],
        "can_see_fees": True,
    },
    "accounts": {
        "label": "Accounts",
        "objects": [
            "v_students", "v_enrollments", "v_batches", "v_courses_with_fee",
            "v_instalments", "v_fee_plans", "v_payments",
        ],
        "actions": ["send_whatsapp", "mark_instalment_paid"],
        "can_see_fees": True,
    },
    "trainer": {
        "label": "Trainer",
        "objects": [
            "t_my_batches", "t_my_students", "t_my_sessions", "t_my_attendance",
            "t_my_assignments", "t_my_submissions", "t_my_recordings", "t_my_timetable",
            "v_courses_public", "v_rooms",
        ],
        "actions": [],
        "can_see_fees": False,
    },
}

# What each role is NOT allowed to see, stated plainly for refusals.
ROLE_RESTRICTIONS = {
    "owner": "Owners can see everything except login credentials.",
    "counselor": "Counselors cannot see payment transaction references, the audit log or trainer contact details.",
    "accounts": "Accounts staff cannot see attendance, assignments, recordings or timetables.",
    "trainer": "Trainers can only see their own batches and students, and can never see fees, payments, EMIs or student phone numbers.",
}

ACTION_DOCS = {
    "send_whatsapp": "Draft WhatsApp messages to students. Needs a recipient query returning student_id, name and phone columns.",
    "mark_instalment_paid": "Mark fee instalments as paid. Needs a query returning instalment id as instalment_id.",
    "apply_timetable": "Apply a re-planned timetable (created by the timetable planner).",
}


# The owner may also read raw tables directly (never users/sessions: they hold credentials).
OWNER_RAW_TABLES = [
    "courses", "trainers", "trainer_availability", "trainer_leaves", "rooms", "batches",
    "students", "enrollments", "class_sessions", "attendance", "recordings", "assignments",
    "submissions", "certificates", "fee_plans", "instalments", "payments", "timetable_slots",
    "pending_actions", "messages_outbox", "audit_log",
]


def allowed_objects(role: str) -> set[str]:
    objs = set(ROLES[role]["objects"])
    if role == "owner":
        objs |= set(OWNER_RAW_TABLES)
    return objs


def can_do(role: str, action_type: str) -> bool:
    return action_type in ROLES.get(role, {}).get("actions", [])


def check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}")
