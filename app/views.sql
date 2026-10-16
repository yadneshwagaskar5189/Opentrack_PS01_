-- =====================================================================
-- Role views. The copilot only ever sees (and can only ever read) the
-- views allowed for the asking user's role. See app/rbac.py.
-- Views never read other views, so the SQLite authorizer can check
-- every read against the allow-list exactly.
-- =====================================================================

-- ---------- Shared, non-sensitive ----------
CREATE VIEW v_courses_public AS
SELECT id, code, name, duration_weeks FROM courses;

CREATE VIEW v_courses_with_fee AS
SELECT id, code, name, duration_weeks, total_fee, emi_count FROM courses;

CREATE VIEW v_rooms AS
SELECT id, name, capacity, has_projector FROM rooms;

CREATE VIEW v_trainers_public AS
SELECT id, name, skills FROM trainers;

CREATE VIEW v_batches AS
SELECT b.id, b.code, b.name AS batch_name, c.code AS course_code, c.name AS course_name,
       b.trainer_id, t.name AS trainer_name, b.mode, b.time_window,
       b.days, b.start_time, b.end_time, r.name AS usual_room,
       b.sessions_per_week, b.session_hours, b.online_seat_limit,
       (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id = b.id AND e.status = 'active'
            AND e.learning_mode = 'online') AS online_students,
       (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id = b.id AND e.status = 'active'
            AND e.learning_mode = 'classroom') AS classroom_students,
       b.start_date, b.end_date, b.status
FROM batches b
JOIN courses c  ON c.id = b.course_id
JOIN trainers t ON t.id = b.trainer_id
LEFT JOIN rooms r ON r.id = b.room_id;

CREATE VIEW v_timetable AS
SELECT s.id, s.week_start, s.day_of_week,
       CASE s.day_of_week WHEN 0 THEN 'Mon' WHEN 1 THEN 'Tue' WHEN 2 THEN 'Wed'
            WHEN 3 THEN 'Thu' WHEN 4 THEN 'Fri' WHEN 5 THEN 'Sat' ELSE 'Sun' END AS day_name,
       s.start_hour, s.end_hour, s.batch_id, b.code AS batch_code,
       s.trainer_id, t.name AS trainer_name, s.room_id, r.name AS room_name, s.is_online
FROM timetable_slots s
JOIN batches b  ON b.id = s.batch_id
JOIN trainers t ON t.id = s.trainer_id
LEFT JOIN rooms r ON r.id = s.room_id;

-- ---------- Student data (owner, counselor, accounts) ----------
CREATE VIEW v_students AS
SELECT id, name, phone, email, city, parent_name, guardian_phone, joined_on FROM students;

CREATE VIEW v_enrollments AS
SELECT e.id, e.student_id, s.name AS student_name, e.batch_id, b.code AS batch_code,
       c.name AS course_name, e.learning_mode, e.status, e.enrolled_on
FROM enrollments e
JOIN students s ON s.id = e.student_id
JOIN batches b  ON b.id = e.batch_id
JOIN courses c  ON c.id = b.course_id;

CREATE VIEW v_class_sessions AS
SELECT cs.id, cs.batch_id, b.code AS batch_code, cs.session_date,
       cs.start_time, cs.end_time, cs.topic, cs.status
FROM class_sessions cs JOIN batches b ON b.id = cs.batch_id;

CREATE VIEW v_attendance AS
SELECT a.id, a.student_id, s.name AS student_name, cs.batch_id, b.code AS batch_code,
       a.session_id, cs.session_date, a.status, a.mode, a.check_in_time, a.online_minutes
FROM attendance a
JOIN students s        ON s.id = a.student_id
JOIN class_sessions cs ON cs.id = a.session_id
JOIN batches b         ON b.id = cs.batch_id;

CREATE VIEW v_assignments AS
SELECT a.id, a.batch_id, b.code AS batch_code, a.title, a.due_date
FROM assignments a JOIN batches b ON b.id = a.batch_id;

CREATE VIEW v_submissions AS
SELECT sb.id, sb.assignment_id, a.title AS assignment_title, a.batch_id, b.code AS batch_code,
       sb.student_id, s.name AS student_name, sb.submitted_on, sb.score, sb.status
FROM submissions sb
JOIN assignments a ON a.id = sb.assignment_id
JOIN batches b     ON b.id = a.batch_id
JOIN students s    ON s.id = sb.student_id;

CREATE VIEW v_recordings AS
SELECT r.id, r.session_id, cs.session_date, cs.batch_id, b.code AS batch_code,
       cs.topic, r.url, r.duration_min, r.views
FROM recordings r
JOIN class_sessions cs ON cs.id = r.session_id
JOIN batches b         ON b.id = cs.batch_id;

-- ---------- Fees (owner, accounts; counselor gets instalments only) ----------
CREATE VIEW v_instalments AS
SELECT i.id, e.student_id, s.name AS student_name, s.phone AS student_phone,
       e.batch_id, b.code AS batch_code, i.instalment_no, i.due_date, i.amount,
       i.paid_amount, (i.amount - i.paid_amount) AS balance, i.status, i.paid_on
FROM instalments i
JOIN fee_plans fp  ON fp.id = i.fee_plan_id
JOIN enrollments e ON e.id = fp.enrollment_id
JOIN students s    ON s.id = e.student_id
JOIN batches b     ON b.id = e.batch_id;

CREATE VIEW v_fee_plans AS
SELECT fp.id, fp.enrollment_id, e.student_id, s.name AS student_name, b.code AS batch_code,
       fp.plan_type, fp.total_amount, fp.discount, fp.instalments_count
FROM fee_plans fp
JOIN enrollments e ON e.id = fp.enrollment_id
JOIN students s    ON s.id = e.student_id
JOIN batches b     ON b.id = e.batch_id;

CREATE VIEW v_payments AS
SELECT p.id, p.student_id, s.name AS student_name, p.instalment_id, p.amount,
       p.paid_on, p.method, p.reference
FROM payments p JOIN students s ON s.id = p.student_id;

-- Bank statement lines (owner, accounts). utr matches v_payments.reference.
CREATE VIEW v_bank_statement AS
SELECT id, txn_ref, txn_date, description, credit, utr, balance FROM bank_transactions;

-- One row per enrollment: attendance and fees, with the certificate rule applied
-- (attendance >= 75% AND fees fully paid).
CREATE VIEW v_certificate_eligibility AS
SELECT x.*,
       CASE WHEN x.attendance_pct >= 75 AND x.fee_balance = 0 THEN 1 ELSE 0 END AS eligible,
       CASE WHEN x.attendance_pct >= 75 AND x.fee_balance = 0 THEN 'Eligible'
            WHEN x.attendance_pct < 75 AND x.fee_balance > 0 THEN 'Attendance below 75% and fees pending'
            WHEN x.attendance_pct < 75 THEN 'Attendance below 75%'
            WHEN x.attendance_pct IS NULL THEN 'No classes recorded yet'
            ELSE 'Fees pending' END AS reason,
       (SELECT certificate_no FROM certificates c WHERE c.enrollment_id = x.enrollment_id) AS certificate_no
FROM (
  SELECT e.id AS enrollment_id, e.student_id, s.name AS student_name, e.batch_id, b.code AS batch_code,
         b.status AS batch_status, e.status AS enrollment_status,
         (SELECT COUNT(*) FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
            WHERE a.student_id = e.student_id AND cs.batch_id = e.batch_id) AS classes_held,
         (SELECT COUNT(*) FROM attendance a JOIN class_sessions cs ON cs.id = a.session_id
            WHERE a.student_id = e.student_id AND cs.batch_id = e.batch_id AND a.status != 'absent') AS classes_attended,
         (SELECT ROUND(100.0 * SUM(a.status != 'absent') / COUNT(*), 1) FROM attendance a
            JOIN class_sessions cs ON cs.id = a.session_id
            WHERE a.student_id = e.student_id AND cs.batch_id = e.batch_id) AS attendance_pct,
         (SELECT COALESCE(SUM(i.amount - i.paid_amount), 0) FROM instalments i
            JOIN fee_plans fp ON fp.id = i.fee_plan_id WHERE fp.enrollment_id = e.id) AS fee_balance
  FROM enrollments e
  JOIN students s ON s.id = e.student_id
  JOIN batches b  ON b.id = e.batch_id
) x;

-- ---------- Trainer: only their own batches, never fees or phone numbers ----------
-- current_trainer_id() is registered per connection from the logged-in user.
CREATE VIEW t_my_batches AS
SELECT b.id, b.code, b.name AS batch_name, c.name AS course_name, b.mode, b.time_window,
       b.days, b.start_time, b.end_time, b.sessions_per_week, b.online_seat_limit,
       (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id = b.id AND e.status = 'active'
            AND e.learning_mode = 'online') AS online_students,
       (SELECT COUNT(*) FROM enrollments e WHERE e.batch_id = b.id AND e.status = 'active'
            AND e.learning_mode = 'classroom') AS classroom_students,
       b.start_date, b.end_date, b.status
FROM batches b JOIN courses c ON c.id = b.course_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_students AS
SELECT s.id AS student_id, s.name, s.email, e.batch_id, b.code AS batch_code,
       e.learning_mode, e.status AS enrollment_status
FROM enrollments e
JOIN students s ON s.id = e.student_id
JOIN batches b  ON b.id = e.batch_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_sessions AS
SELECT cs.id, cs.batch_id, b.code AS batch_code, cs.session_date,
       cs.start_time, cs.end_time, cs.topic, cs.status
FROM class_sessions cs JOIN batches b ON b.id = cs.batch_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_attendance AS
SELECT a.id, a.student_id, s.name AS student_name, cs.batch_id, b.code AS batch_code,
       cs.session_date, a.status, a.mode, a.check_in_time, a.online_minutes
FROM attendance a
JOIN students s        ON s.id = a.student_id
JOIN class_sessions cs ON cs.id = a.session_id
JOIN batches b         ON b.id = cs.batch_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_assignments AS
SELECT a.id, a.batch_id, b.code AS batch_code, a.title, a.due_date
FROM assignments a JOIN batches b ON b.id = a.batch_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_submissions AS
SELECT sb.id, sb.assignment_id, a.title AS assignment_title, b.code AS batch_code,
       sb.student_id, s.name AS student_name, sb.submitted_on, sb.score, sb.status
FROM submissions sb
JOIN assignments a ON a.id = sb.assignment_id
JOIN batches b     ON b.id = a.batch_id
JOIN students s    ON s.id = sb.student_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_recordings AS
SELECT r.id, cs.session_date, b.code AS batch_code, cs.topic, r.url, r.duration_min, r.views
FROM recordings r
JOIN class_sessions cs ON cs.id = r.session_id
JOIN batches b         ON b.id = cs.batch_id
WHERE b.trainer_id = current_trainer_id();

CREATE VIEW t_my_timetable AS
SELECT s.id, s.week_start, s.day_of_week,
       CASE s.day_of_week WHEN 0 THEN 'Mon' WHEN 1 THEN 'Tue' WHEN 2 THEN 'Wed'
            WHEN 3 THEN 'Thu' WHEN 4 THEN 'Fri' WHEN 5 THEN 'Sat' ELSE 'Sun' END AS day_name,
       s.start_hour, s.end_hour, b.code AS batch_code, r.name AS room_name, s.is_online
FROM timetable_slots s
JOIN batches b ON b.id = s.batch_id
LEFT JOIN rooms r ON r.id = s.room_id
WHERE s.trainer_id = current_trainer_id();
