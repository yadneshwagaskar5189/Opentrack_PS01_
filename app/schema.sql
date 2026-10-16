-- =====================================================================
-- Institute Operations Copilot - database schema (SQLite)
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------- People & access ----------
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('owner','counselor','trainer','accounts')),
    display_name  TEXT NOT NULL,
    trainer_id    INTEGER REFERENCES trainers(id)      -- set only for role = trainer
);

CREATE TABLE sessions (                                 -- login tokens
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

-- ---------- Academics ----------
CREATE TABLE courses (
    id             INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,                -- e.g. JFS
    name           TEXT NOT NULL,                       -- e.g. Java Full Stack
    duration_weeks INTEGER NOT NULL,
    total_fee      INTEGER NOT NULL,                    -- rupees
    emi_count      INTEGER NOT NULL DEFAULT 1           -- usual number of instalments
);

CREATE TABLE trainers (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL,
    email  TEXT,
    phone  TEXT,
    skills TEXT NOT NULL      -- comma list: course codes ('JFS,SPR') or skill words found in course names ('Java,SQL')
);

-- Weekly availability. day_of_week: 0=Mon ... 6=Sun. Hours are 24h clock (19.5 = 19:30).
CREATE TABLE trainer_availability (
    id          INTEGER PRIMARY KEY,
    trainer_id  INTEGER NOT NULL REFERENCES trainers(id),
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_hour  INTEGER NOT NULL,
    end_hour    INTEGER NOT NULL
);

-- One-off unavailability (leave, sickness) for specific dates
CREATE TABLE trainer_leaves (
    id         INTEGER PRIMARY KEY,
    trainer_id INTEGER NOT NULL REFERENCES trainers(id),
    leave_date TEXT NOT NULL,                           -- YYYY-MM-DD
    reason     TEXT
);

CREATE TABLE rooms (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    capacity      INTEGER NOT NULL,
    has_projector INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE batches (
    id                INTEGER PRIMARY KEY,
    code              TEXT NOT NULL UNIQUE,             -- e.g. JAVA-MOR-01 or B01
    name              TEXT,                             -- e.g. Java Full Stack - Morning
    course_id         INTEGER NOT NULL REFERENCES courses(id),
    trainer_id        INTEGER NOT NULL REFERENCES trainers(id),
    mode              TEXT NOT NULL CHECK (mode IN ('classroom','online','hybrid')),
    time_window       TEXT NOT NULL CHECK (time_window IN ('morning','afternoon','evening','weekend')),
    sessions_per_week INTEGER NOT NULL,
    session_hours     INTEGER NOT NULL DEFAULT 2,       -- may be fractional, e.g. 1.5
    online_seat_limit INTEGER NOT NULL DEFAULT 0,       -- max online participants (online/hybrid)
    days              TEXT,                             -- usual days, e.g. 'Mon,Wed,Fri'
    start_time        TEXT,                             -- usual start, HH:MM
    end_time          TEXT,                             -- usual end, HH:MM
    room_id           INTEGER REFERENCES rooms(id),     -- usual room
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('upcoming','active','completed'))
);

CREATE TABLE students (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    phone          TEXT NOT NULL,                       -- +91XXXXXXXXXX
    email          TEXT,
    city           TEXT,
    parent_name    TEXT,
    guardian_phone TEXT,
    joined_on      TEXT NOT NULL,
    real_contact   INTEGER NOT NULL DEFAULT 0           -- 1 = phone typed in by staff in the app: WhatsApp goes
                                                        --     to it; 0 = imported sample data (made-up number)
);

CREATE TABLE enrollments (
    id            INTEGER PRIMARY KEY,
    student_id    INTEGER NOT NULL REFERENCES students(id),
    batch_id      INTEGER NOT NULL REFERENCES batches(id),
    enrolled_on   TEXT NOT NULL,
    learning_mode TEXT NOT NULL CHECK (learning_mode IN ('classroom','online')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','dropped'))
);

-- A class that actually happened (or is scheduled) on a date
CREATE TABLE class_sessions (
    id           INTEGER PRIMARY KEY,
    batch_id     INTEGER NOT NULL REFERENCES batches(id),
    session_date TEXT NOT NULL,
    start_time   TEXT NOT NULL,                         -- HH:MM
    end_time     TEXT NOT NULL,
    topic        TEXT,
    status       TEXT NOT NULL DEFAULT 'held' CHECK (status IN ('held','cancelled','scheduled'))
);

-- Hybrid attendance: classroom check-ins and online join logs in one table
CREATE TABLE attendance (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES class_sessions(id),
    student_id     INTEGER NOT NULL REFERENCES students(id),
    status         TEXT NOT NULL CHECK (status IN ('present','late','absent')),
    mode           TEXT CHECK (mode IN ('classroom','online')),   -- NULL when absent
    check_in_time  TEXT,                                -- classroom check-in HH:MM
    online_minutes INTEGER                              -- minutes connected (online)
);

CREATE TABLE recordings (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES class_sessions(id),
    url          TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    views        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE assignments (
    id       INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    title    TEXT NOT NULL,
    due_date TEXT NOT NULL
);

CREATE TABLE submissions (
    id            INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    student_id    INTEGER NOT NULL REFERENCES students(id),
    submitted_on  TEXT NOT NULL,
    score         INTEGER,                              -- out of 100
    status        TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','late'))
);

CREATE TABLE certificates (
    id            INTEGER PRIMARY KEY,
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id),
    issued_on     TEXT NOT NULL,
    certificate_no TEXT NOT NULL UNIQUE
);

-- ---------- Fees ----------
CREATE TABLE fee_plans (
    id                 INTEGER PRIMARY KEY,
    enrollment_id      INTEGER NOT NULL REFERENCES enrollments(id),
    plan_type          TEXT NOT NULL CHECK (plan_type IN ('full','emi')),
    total_amount       INTEGER NOT NULL,
    discount           INTEGER NOT NULL DEFAULT 0,
    instalments_count  INTEGER NOT NULL
);

CREATE TABLE instalments (
    id             INTEGER PRIMARY KEY,
    fee_plan_id    INTEGER NOT NULL REFERENCES fee_plans(id),
    instalment_no  INTEGER NOT NULL,
    due_date       TEXT NOT NULL,
    amount         INTEGER NOT NULL,
    paid_amount    INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL CHECK (status IN ('paid','due','overdue','partial')),
    paid_on        TEXT
);

CREATE TABLE payments (
    id            INTEGER PRIMARY KEY,
    instalment_id INTEGER REFERENCES instalments(id),
    student_id    INTEGER NOT NULL REFERENCES students(id),
    amount        INTEGER NOT NULL,
    paid_on       TEXT NOT NULL,
    method        TEXT NOT NULL CHECK (method IN ('UPI','bank_transfer','cash','card')),
    reference     TEXT,                                 -- UPI ref / UTR
    receipt_no    TEXT                                  -- set when the payment is saved through an approval
);

-- Bank statement lines, for fee reconciliation (owner and accounts only)
CREATE TABLE bank_transactions (
    id          INTEGER PRIMARY KEY,
    txn_ref     TEXT,                                   -- bank's own id, e.g. BT0042
    txn_date    TEXT NOT NULL,
    description TEXT NOT NULL,                          -- e.g. UPI/412398765012/SURESH GUPTA
    credit      INTEGER NOT NULL,
    utr         TEXT,                                   -- UPI ref / UTR, matches payments.reference
    balance     INTEGER
);

-- ---------- Timetable ----------
CREATE TABLE timetable_slots (
    id          INTEGER PRIMARY KEY,
    week_start  TEXT NOT NULL,                          -- Monday, YYYY-MM-DD
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    trainer_id  INTEGER NOT NULL REFERENCES trainers(id),
    room_id     INTEGER REFERENCES rooms(id),           -- NULL for fully online
    day_of_week INTEGER NOT NULL,
    start_hour  INTEGER NOT NULL,
    end_hour    INTEGER NOT NULL,
    is_online   INTEGER NOT NULL DEFAULT 0              -- 1 if an online licence is used
);

-- Requests for new batches, waiting to be scheduled
CREATE TABLE batch_requests (
    id                  INTEGER PRIMARY KEY,
    code                TEXT NOT NULL,                  -- e.g. NB01
    course_name         TEXT NOT NULL,
    expected_students   INTEGER NOT NULL,
    online_seats_needed INTEGER NOT NULL DEFAULT 0,
    preferred_days      TEXT,                           -- e.g. 'Tue,Thu'
    preferred_start     TEXT,                           -- HH:MM
    preferred_end       TEXT,
    mode                TEXT,
    required_skill      TEXT,
    note                TEXT
);

-- Small key/value settings, e.g. the institute's name
CREATE TABLE app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---------- Copilot governance ----------
CREATE TABLE pending_actions (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    created_by   INTEGER NOT NULL REFERENCES users(id),
    action_type  TEXT NOT NULL,                         -- send_whatsapp | mark_instalment_paid | apply_timetable
    title        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','failed')),
    decided_by   INTEGER REFERENCES users(id),
    decided_at   TEXT,
    result_json  TEXT
);

CREATE TABLE messages_outbox (
    id         INTEGER PRIMARY KEY,
    action_id  INTEGER REFERENCES pending_actions(id),
    channel    TEXT NOT NULL DEFAULT 'whatsapp',
    student_id INTEGER REFERENCES students(id),
    to_phone   TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL,                           -- queued | simulated | sent ... | failed: ...
    created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    user_id     INTEGER,
    username    TEXT,
    role        TEXT,
    event_type  TEXT NOT NULL,   -- login | ask | query_ok | query_blocked | refusal | action_proposed | action_approved | action_rejected | timetable_replan | error
    question    TEXT,
    sql_text    TEXT,
    outcome     TEXT,
    detail_json TEXT,
    prev_hash   TEXT,            -- seal of the entry before this one (tamper evidence, see audit.py)
    hash        TEXT             -- HMAC of this entry + prev_hash, keyed by a secret kept outside the database
);

CREATE INDEX idx_att_student ON attendance(student_id);
CREATE INDEX idx_att_session ON attendance(session_id);
CREATE INDEX idx_sess_batch  ON class_sessions(batch_id, session_date);
CREATE INDEX idx_inst_plan   ON instalments(fee_plan_id);
CREATE INDEX idx_enr_student ON enrollments(student_id);
CREATE INDEX idx_enr_batch   ON enrollments(batch_id);
