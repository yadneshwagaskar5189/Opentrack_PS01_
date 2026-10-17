"""30 everyday questions with checked answers: how the copilot learns the institute's language.

This is not model training. Each entry is a question staff really ask, written the way they ask
it (including "lec", "sir", "aaj"), with a SQL answer we tested against the real views. For every
new question the copilot picks the closest few entries the user's role is allowed to run and puts
them in the prompt as worked examples. The offline mock (LLM_PROVIDER=mock) answers these
questions directly. tests/test_question_bank.py runs every entry for every role.

SQL may use these dates, filled in at run time:
  {today} {tomorrow} {yesterday}   {week_end} = today + 6 ("this week")
  {next_mon} {next_sun} = next calendar week     {month_start} = 1st of this month
"""
import json
import math
import re
from datetime import timedelta

from . import config, rbac

D01 = ("Which students in the Java batch have missed three or more classes and have an EMI due this week? "
       "Draft a WhatsApp reminder for each.")

# id, topic, q (the question), alt (other ways people ask it), sql (staff roles),
# trainer (the same answer from the trainer's own views; omitted = trainers can't get this), why, plan (extra JSON)
BANK: list[dict] = [
    # ------------------------------------------------------------ fees
    dict(id="fee_structure", topic="Fees", q="What is the total fee structure?",
         alt=["fees structure of all courses", "course fees and EMI plan", "how much is the fee for each course",
              "fee kitna hai", "course wise fees"],
         sql="SELECT code AS course_code, name AS course, duration_weeks, total_fee, emi_count AS emis, "
             "CAST(ROUND(total_fee * 1.0 / emi_count) AS INTEGER) AS per_emi FROM v_courses_with_fee "
             "ORDER BY total_fee DESC",
         why="Every course with its total fee, number of EMIs and the amount of each EMI."),
    dict(id="fee_one_course", topic="Fees", q="What is the fee for Java Full Stack and how many EMIs?",
         alt=["java course fee", "fee of the python course", "how many installments for java", "java fees emi"],
         sql="SELECT name AS course, total_fee, emi_count AS emis, CAST(ROUND(total_fee * 1.0 / emi_count) AS INTEGER) "
             "AS per_emi, duration_weeks FROM v_courses_with_fee WHERE name LIKE '%Java%'",
         why="Fee and EMI plan of the Java course."),
    dict(id="emi_due_week", topic="Fees", q="Which EMIs are due this week?",
         alt=["emi due this week", "whose installment is due", "upcoming emi payments", "is hafte ki emi"],
         sql="SELECT student_name, batch_code, instalment_no, due_date, balance FROM v_instalments "
             "WHERE status != 'paid' AND due_date BETWEEN '{today}' AND '{week_end}' ORDER BY due_date, student_name",
         why="Unpaid instalments due today or in the next 6 days."),
    dict(id="overdue", topic="Fees", q="Who has overdue fees?",
         alt=["fee defaulters", "list of defaulters", "overdue emi", "who has not paid the fees", "late fees"],
         sql="SELECT student_name, batch_code, instalment_no, due_date, balance, status FROM v_instalments "
             "WHERE status IN ('overdue', 'partial') AND due_date < '{today}' ORDER BY balance DESC",
         why="Instalments past their due date that are not fully paid, biggest first."),
    dict(id="pending_by_batch", topic="Fees", q="How much fee is still pending in each batch?",
         alt=["total pending fees", "batch wise pending fee", "outstanding fees", "fees remaining"],
         sql="SELECT batch_code, COUNT(DISTINCT student_id) AS students, SUM(balance) AS pending, "
             "SUM(CASE WHEN due_date < '{today}' THEN balance ELSE 0 END) AS overdue FROM v_instalments "
             "WHERE status != 'paid' GROUP BY batch_code ORDER BY pending DESC",
         why="Unpaid fee balance per batch, and how much of it is already overdue."),
    dict(id="collected_month", topic="Fees", q="How much fee did we collect this month?",
         alt=["collection this month", "payments received this month", "monthly fee collection", "revenue this month"],
         sql="SELECT method, COUNT(*) AS payments, SUM(amount) AS collected FROM v_payments "
             "WHERE paid_on BETWEEN '{month_start}' AND '{today}' GROUP BY method ORDER BY collected DESC",
         why="Payments received since the 1st of this month, by payment method."),
    dict(id="student_fee", topic="Fees", q="Show the fee status of Rohan Gupta",
         alt=["fee details of a student", "how much has rohan paid", "emi status of a student", "student fee balance"],
         sql="SELECT student_name, batch_code, instalment_no, due_date, amount, paid_amount, balance, status "
             "FROM v_instalments WHERE student_name LIKE '%Rohan Gupta%' ORDER BY batch_code, instalment_no",
         why="All instalments of one student: paid, due and overdue."),
    dict(id="bank_unmatched", topic="Fees", q="Which bank credits don't match any payment?",
         alt=["unmatched bank entries", "reconcile the bank statement", "utr not found in payments"],
         sql="SELECT b.txn_date, b.description, b.credit, b.utr FROM v_bank_statement b "
             "LEFT JOIN v_payments p ON p.reference = b.utr WHERE p.id IS NULL ORDER BY b.txn_date",
         why="Bank credits whose UTR is not recorded as a fee payment."),
    # ------------------------------------------------------------ today and the timetable
    dict(id="today_lectures", topic="Today & timetable", q="What are today's lectures?",
         alt=["today's lec timetable", "aaj ke lecture", "today's classes", "today's schedule",
              "lectures today with time", "today ka timetable"],
         sql="SELECT start_time, end_time, batch_code, course_name, trainer_name, room_name, status "
             "FROM v_schedule WHERE class_date = '{today}' ORDER BY start_time",
         trainer="SELECT start_time, end_time, batch_code, course_name, room_name, status FROM t_my_schedule "
                 "WHERE class_date = '{today}' ORDER BY start_time",
         why="Today's classes with time, batch, trainer and room."),
    dict(id="today_trainers", topic="Today & timetable", q="Which trainer is taking today's lectures?",
         alt=["which sir is allotted today", "which sir is alloted today", "who is teaching today",
              "kaunse sir aaj lecture lenge", "today's faculty", "which sir has lecture today"],
         sql="SELECT trainer_name, COUNT(*) AS lectures, GROUP_CONCAT(batch_code || ' ' || start_time || '-' || "
             "end_time || ' in ' || room_name, ', ') AS classes, MAX(trainer_on_leave) AS on_leave "
             "FROM v_schedule WHERE class_date = '{today}' GROUP BY trainer_name ORDER BY MIN(start_time)",
         why="Each trainer teaching today and their classes (on_leave = 1 means a substitute is needed)."),
    dict(id="today_course_time", topic="Today & timetable", q="What time is the Java lecture today?",
         alt=["java lec time today", "when is the python class today", "today's lecture time", "lec kitne baje hai"],
         sql="SELECT start_time, end_time, batch_code, course_name, trainer_name, room_name FROM v_schedule "
             "WHERE class_date = '{today}' AND course_name LIKE '%Java%' ORDER BY start_time",
         trainer="SELECT start_time, end_time, batch_code, course_name, room_name FROM t_my_schedule "
                 "WHERE class_date = '{today}' AND course_name LIKE '%Java%' ORDER BY start_time",
         why="Today's Java class: time, trainer and room."),
    dict(id="tomorrow", topic="Today & timetable", q="What is tomorrow's timetable?",
         alt=["tomorrow's lectures", "kal ke lecture", "classes tomorrow", "tomorrow's schedule"],
         sql="SELECT start_time, end_time, batch_code, course_name, trainer_name, room_name FROM v_schedule "
             "WHERE class_date = '{tomorrow}' ORDER BY start_time",
         trainer="SELECT start_time, end_time, batch_code, course_name, room_name FROM t_my_schedule "
                 "WHERE class_date = '{tomorrow}' ORDER BY start_time",
         why="Tomorrow's classes with time, trainer and room."),
    dict(id="week_lectures", topic="Today & timetable", q="Show all lectures in the next 7 days",
         alt=["this week's lectures", "lectures this week", "upcoming classes this week", "my classes this week",
              "timetable this week", "this week's schedule"],
         sql="SELECT class_date, day_name, start_time, end_time, batch_code, trainer_name, room_name FROM v_schedule "
             "WHERE class_date BETWEEN '{today}' AND '{week_end}' ORDER BY class_date, start_time",
         trainer="SELECT class_date, day_name, start_time, end_time, batch_code, room_name FROM t_my_schedule "
                 "WHERE class_date BETWEEN '{today}' AND '{week_end}' ORDER BY class_date, start_time",
         why="Every class from today to {week_end}."),
    dict(id="next_week", topic="Today & timetable", q="Show next week's timetable",
         alt=["next week schedule", "timetable for next week", "next week lectures", "agle hafte ka timetable"],
         sql="SELECT class_date, day_name, start_time, end_time, batch_code, trainer_name, room_name, trainer_on_leave "
             "FROM v_schedule WHERE class_date BETWEEN '{next_mon}' AND '{next_sun}' ORDER BY class_date, start_time",
         trainer="SELECT class_date, day_name, start_time, end_time, batch_code, room_name FROM t_my_schedule "
                 "WHERE class_date BETWEEN '{next_mon}' AND '{next_sun}' ORDER BY class_date, start_time",
         why="Classes from Monday {next_mon} to Sunday {next_sun}."),
    # ------------------------------------------------------------ trainers and rooms
    dict(id="trainer_batches", topic="Trainers & rooms", q="Which batches does Rahul sir teach?",
         alt=["rahul sir batches", "what does meera teach", "batches of a trainer", "sir ke batches"],
         sql="SELECT code, batch_name, days, start_time, end_time, usual_room, status FROM v_batches "
             "WHERE trainer_name LIKE '%Rahul%' ORDER BY start_time",
         why="Batches whose regular trainer is Rahul, with days, time and room."),
    dict(id="who_teaches", topic="Trainers & rooms", q="Which trainer teaches which batch?",
         alt=["trainer allotment", "batch wise trainer", "which sir is allotted to which batch", "which sir is alloted",
              "faculty list", "who teaches python"],
         sql="SELECT code, course_name, trainer_name, days, start_time, end_time, usual_room FROM v_batches "
             "WHERE status != 'completed' ORDER BY code",
         why="Running and upcoming batches with their trainer, days, time and room."),
    dict(id="leave_affected", topic="Trainers & rooms", q="Which classes are affected by trainer leave?",
         alt=["trainer on leave", "which sir is on leave", "substitute needed", "classes without a trainer"],
         sql="SELECT class_date, day_name, start_time, end_time, batch_code, trainer_name, room_name FROM v_schedule "
             "WHERE trainer_on_leave = 1 AND class_date >= '{today}' ORDER BY class_date, start_time",
         why="Upcoming classes whose trainer is on leave that day (re-plan them on the Timetable page)."),
    dict(id="double_booked", topic="Trainers & rooms", q="Is any trainer double-booked this week?",
         alt=["timetable clash", "trainer clash", "overlapping classes", "same sir two classes"],
         sql="SELECT a.trainer_name, a.day_name, a.batch_code || ' ' || ba.short_name AS class_1, a.start_hour AS class_1_start, "
             "a.end_hour AS class_1_end, b.batch_code || ' ' || bb.short_name AS class_2, b.start_hour AS class_2_start, "
             "b.end_hour AS class_2_end FROM v_timetable a JOIN v_timetable b ON a.trainer_id = b.trainer_id "
             "AND a.day_of_week = b.day_of_week AND a.id < b.id AND a.start_hour < b.end_hour AND b.start_hour < a.end_hour "
             "JOIN v_batches ba ON ba.id = a.batch_id JOIN v_batches bb ON bb.id = b.batch_id ORDER BY a.day_of_week",
         trainer="SELECT a.day_name, a.start_hour, a.batch_code AS class_1, b.batch_code AS class_2 FROM t_my_timetable a "
                 "JOIN t_my_timetable b ON a.day_of_week = b.day_of_week AND a.id < b.id "
                 "AND a.start_hour < b.end_hour AND b.start_hour < a.end_hour",
         why="Trainers with two classes at the same time in the planning week."),
    dict(id="free_room", topic="Trainers & rooms", q="Which room is free today at 6 pm?",
         alt=["empty classroom today evening", "free lab today", "room available today", "which room is free now",
              "kaunsa room khali hai"],
         sql="SELECT name AS room, capacity, has_projector FROM v_rooms WHERE name NOT IN (SELECT room_name "
             "FROM v_schedule WHERE class_date = '{today}' AND start_time < '19:00' AND end_time > '18:00') "
             "ORDER BY capacity DESC",
         why="Rooms with no class between 18:00 and 19:00 today."),
    # ------------------------------------------------------------ students and batches
    dict(id="students_per_batch", topic="Students", q="How many students are in each batch?",
         alt=["student count", "batch strength", "number of students per batch", "how many students in my batches",
              "how many students are enrolled in each batch", "batch wise enrolment"],
         sql="SELECT code, short_name, classroom_students + online_students AS enrolled, classroom_students, "
             "online_students FROM v_batches WHERE status != 'completed' ORDER BY code",
         trainer="SELECT code, short_name, classroom_students + online_students AS enrolled, classroom_students, "
                 "online_students FROM t_my_batches ORDER BY code",
         why="Active students per batch, classroom and online."),
    dict(id="online_seats", topic="Students", q="How many online seats are left in each batch?",
         alt=["online seats left", "is the online batch full", "seats available online"],
         sql="SELECT code, short_name, online_seat_limit, online_students, online_seat_limit - online_students "
             "AS online_seats_left FROM v_batches WHERE status != 'completed' ORDER BY online_seats_left",
         trainer="SELECT code, online_seat_limit, online_students, online_seat_limit - online_students AS online_seats_left "
                 "FROM t_my_batches ORDER BY online_seats_left",
         why="Online seat limit, seats taken and seats left per batch."),
    dict(id="student_details", topic="Students", q="Show details of student Aarav Sharma",
         alt=["student contact details", "phone number of a student", "which batch is aarav in", "student profile"],
         sql="SELECT s.name, s.phone, s.email, s.city, s.parent_name, s.guardian_phone, e.batch_code, e.course_name, "
             "e.learning_mode, e.status FROM v_students s JOIN v_enrollments e ON e.student_id = s.id "
             "WHERE s.name LIKE '%Aarav Sharma%'",
         trainer="SELECT name, email, batch_code, learning_mode, enrollment_status FROM t_my_students "
                 "WHERE name LIKE '%Aarav Sharma%'",
         why="Contact details and batches of one student."),
    dict(id="upcoming_batches", topic="Students", q="Which new batches are starting soon?",
         alt=["upcoming batches", "new batch start date", "next batch starting"],
         sql="SELECT code, batch_name, trainer_name, start_date, days, start_time, end_time, "
             "classroom_students + online_students AS enrolled FROM v_batches WHERE start_date >= '{today}' "
             "ORDER BY start_date",
         why="Batches that start today or later, with trainer, timing and enrolments so far."),
    # ------------------------------------------------------------ attendance and academics
    dict(id="yesterday_attendance", topic="Attendance & academics", q="What was yesterday's attendance?",
         alt=["attendance yesterday", "kal ki attendance", "how many were present yesterday"],
         sql="SELECT batch_code, SUM(status = 'present') AS present, SUM(status = 'late') AS late, "
             "SUM(status = 'absent') AS absent, ROUND(100.0 * SUM(status != 'absent') / COUNT(*), 1) AS attendance_pct "
             "FROM v_attendance WHERE session_date = '{yesterday}' GROUP BY batch_code ORDER BY batch_code",
         trainer="SELECT batch_code, SUM(status = 'present') AS present, SUM(status = 'late') AS late, "
                 "SUM(status = 'absent') AS absent FROM t_my_attendance WHERE session_date = '{yesterday}' "
                 "GROUP BY batch_code",
         why="Present, late and absent per batch for yesterday's classes."),
    dict(id="last_class_absent", topic="Attendance & academics", q="Who was absent in the last class of B02?",
         alt=["absent students last lecture", "who missed the last class", "last lec absentees"],
         sql="SELECT student_name, session_date FROM v_attendance WHERE batch_code = 'B02' AND status = 'absent' "
             "AND session_date = (SELECT MAX(session_date) FROM v_attendance WHERE batch_code = 'B02') "
             "ORDER BY student_name",
         trainer="SELECT student_name, session_date FROM t_my_attendance WHERE batch_code = 'B02' AND status = 'absent' "
                 "AND session_date = (SELECT MAX(session_date) FROM t_my_attendance WHERE batch_code = 'B02') "
                 "ORDER BY student_name",
         why="Students absent in B02's most recent class."),
    dict(id="low_attendance", topic="Attendance & academics", q="Which students have attendance below 75%?",
         alt=["low attendance students", "attendance shortage", "students below 75 percent"],
         sql="SELECT student_name, batch_code, classes_held, classes_attended, attendance_pct "
             "FROM v_certificate_eligibility WHERE enrollment_status = 'active' AND attendance_pct < 75 "
             "ORDER BY attendance_pct",
         trainer="SELECT student_name, batch_code, COUNT(*) AS classes_held, SUM(status != 'absent') AS attended, "
                 "ROUND(100.0 * SUM(status != 'absent') / COUNT(*), 1) AS attendance_pct FROM t_my_attendance "
                 "GROUP BY student_id, batch_code HAVING attendance_pct < 75 ORDER BY attendance_pct",
         why="Active students whose attendance is under 75%, lowest first."),
    dict(id="missing_submission", topic="Attendance & academics", q="Who has not submitted the latest assignment in B01?",
         alt=["assignment not submitted", "pending assignments", "who didn't submit homework"],
         sql="SELECT e.student_name, a.title, a.due_date FROM v_assignments a JOIN v_enrollments e "
             "ON e.batch_id = a.batch_id AND e.status = 'active' WHERE a.batch_code = 'B01' AND a.id = "
             "(SELECT id FROM v_assignments WHERE batch_code = 'B01' ORDER BY due_date DESC, id DESC LIMIT 1) "
             "AND NOT EXISTS (SELECT 1 FROM v_submissions s WHERE s.assignment_id = a.id AND s.student_id = e.student_id) "
             "ORDER BY e.student_name",
         trainer="SELECT st.name AS student_name, a.title, a.due_date FROM t_my_assignments a JOIN t_my_students st "
                 "ON st.batch_id = a.batch_id AND st.enrollment_status = 'active' WHERE a.batch_code = 'B01' AND a.id = "
                 "(SELECT id FROM t_my_assignments WHERE batch_code = 'B01' ORDER BY due_date DESC, id DESC LIMIT 1) "
                 "AND NOT EXISTS (SELECT 1 FROM t_my_submissions s WHERE s.assignment_id = a.id "
                 "AND s.student_id = st.student_id) ORDER BY st.name",
         why="Active B01 students with no submission for B01's latest assignment."),
    dict(id="missing_recordings", topic="Attendance & academics", q="Which classes are missing a recording?",
         alt=["recording not uploaded", "classes without recording", "missing lecture videos"],
         sql="SELECT s.batch_code, s.session_date, s.topic FROM v_class_sessions s LEFT JOIN v_recordings r "
             "ON r.session_id = s.id WHERE r.id IS NULL AND s.session_date < '{today}' ORDER BY s.batch_code, s.session_date",
         trainer="SELECT s.batch_code, s.session_date, s.topic FROM t_my_sessions s WHERE NOT EXISTS (SELECT 1 FROM "
                 "t_my_recordings r WHERE r.batch_code = s.batch_code AND r.session_date = s.session_date) "
                 "AND s.session_date < '{today}' ORDER BY s.batch_code, s.session_date",
         why="Classes already held that have no recording uploaded."),
    # ------------------------------------------------------------ certificates and reminders
    dict(id="certificates", topic="Reminders & certificates", q="Who is eligible for a certificate?",
         alt=["certificate eligible students", "who can get the certificate", "completion certificate list"],
         sql="SELECT student_name, batch_code, attendance_pct, fee_balance, eligible, reason FROM v_certificate_eligibility "
             "WHERE batch_status = 'completed' ORDER BY eligible DESC, student_name",
         why="Certificate rule: attendance of at least 75% and fees fully paid."),
    dict(id="d01_reminder", topic="Reminders & certificates", q=D01,
         alt=["java students absent 3 classes with emi due, send whatsapp", "remind students who missed classes and have emi due"],
         sql="SELECT s.id AS student_id, s.name, s.phone, e.batch_code, COUNT(DISTINCT a.id) AS classes_missed, "
             "i.due_date, i.balance AS amount_due FROM v_students s "
             "JOIN v_enrollments e ON e.student_id = s.id AND e.status = 'active' AND e.course_name LIKE '%Java%' "
             "JOIN v_attendance a ON a.student_id = s.id AND a.batch_id = e.batch_id AND a.status = 'absent' "
             "JOIN v_instalments i ON i.student_id = s.id AND i.batch_id = e.batch_id AND i.status != 'paid' "
             "AND i.due_date BETWEEN '{today}' AND '{week_end}' GROUP BY s.id, i.id HAVING COUNT(DISTINCT a.id) >= 3 "
             "ORDER BY classes_missed DESC",
         plan={"type": "action", "action_type": "send_whatsapp", "title": "EMI + attendance reminder",
               "message_template": "Hi {name}, this is {institute}. We noticed you've missed {classes_missed} classes in "
                                   "{batch_code}, and your EMI of ₹{amount_due} is due on {due_date}. Recordings are on "
                                   "the portal; do reach out if you need help. Thank you!"},
         why="Java students with 3+ absences and an unpaid EMI due this week, one reminder each."),
]

assert len(BANK) == 30, len(BANK)

# More checked examples, used only as worked examples in the prompt and by the offline mode (not shown in the
# question list). They cover totals, at-risk reasons, seat checks, parent messages, reconciliation and the like.
_RISK = ("SELECT student_name, batch_code, attendance_pct, absences_last_2_weeks, overdue_emis, missing_assignments, "
         "trim(CASE WHEN attendance_pct < 75 THEN 'attendance ' || attendance_pct || '%; ' ELSE '' END || "
         "CASE WHEN absences_last_2_weeks >= 3 THEN absences_last_2_weeks || ' absences in the last 2 weeks; ' ELSE '' END || "
         "CASE WHEN overdue_emis > 0 THEN overdue_emis || ' overdue EMI(s); ' ELSE '' END || "
         "CASE WHEN missing_assignments > 0 THEN missing_assignments || ' assignment(s) not submitted' ELSE '' END, '; ') "
         "AS reasons FROM (SELECT e.student_name, e.batch_code, "
         "(SELECT c.attendance_pct FROM v_certificate_eligibility c WHERE c.enrollment_id = e.id) AS attendance_pct, "
         "(SELECT COUNT(*) FROM v_attendance a WHERE a.student_id = e.student_id AND a.batch_id = e.batch_id "
         "AND a.status = 'absent' AND a.session_date >= date('{today}', '-14 days')) AS absences_last_2_weeks, "
         "(SELECT COUNT(*) FROM v_instalments i WHERE i.student_id = e.student_id AND i.batch_id = e.batch_id "
         "AND i.status IN ('overdue', 'partial') AND i.due_date < '{today}') AS overdue_emis, "
         "(SELECT COUNT(*) FROM v_assignments s WHERE s.batch_id = e.batch_id AND s.due_date < '{today}' AND NOT EXISTS "
         "(SELECT 1 FROM v_submissions sb WHERE sb.assignment_id = s.id AND sb.student_id = e.student_id)) AS missing_assignments "
         "FROM v_enrollments e WHERE e.status = 'active') "
         "WHERE attendance_pct < 75 OR absences_last_2_weeks >= 3 OR overdue_emis > 0 "
         "ORDER BY (attendance_pct < 75) + (absences_last_2_weeks >= 3) + 2 * overdue_emis + (missing_assignments > 0) DESC, "
         "attendance_pct")
_RISK_T = ("SELECT student_name, batch_code, attendance_pct, absences_last_2_weeks, "
           "trim(CASE WHEN attendance_pct < 75 THEN 'attendance ' || attendance_pct || '%; ' ELSE '' END || "
           "CASE WHEN absences_last_2_weeks >= 3 THEN absences_last_2_weeks || ' absences in the last 2 weeks' ELSE '' END, '; ') "
           "AS reasons FROM (SELECT student_name, batch_code, ROUND(100.0 * SUM(status != 'absent') / COUNT(*), 1) "
           "AS attendance_pct, SUM(status = 'absent' AND session_date >= date('{today}', '-14 days')) AS absences_last_2_weeks "
           "FROM t_my_attendance GROUP BY student_id, batch_code) WHERE attendance_pct < 75 OR absences_last_2_weeks >= 3 "
           "ORDER BY attendance_pct")
_RECONCILE = ("SELECT b.txn_date, b.description, b.credit, b.utr, CASE "
              "WHEN (SELECT COUNT(*) FROM v_bank_statement d WHERE d.utr = b.utr) > 1 THEN 'duplicate credit' "
              "WHEN p.id IS NULL AND (b.utr IS NULL OR lower(b.description) LIKE '%interest%') THEN 'not a fee (bank interest)' "
              "WHEN p.id IS NULL THEN 'unmatched: no payment has this UTR' "
              "WHEN p.amount != b.credit THEN 'amount differs: ' || p.amount || ' recorded' ELSE 'matched' END AS status "
              "FROM v_bank_statement b LEFT JOIN v_payments p ON p.reference = b.utr WHERE status != 'matched' "
              "UNION ALL SELECT p.paid_on, 'Payment recorded for ' || p.student_name, p.amount, p.reference, "
              "'recorded but not in the bank statement' FROM v_payments p WHERE p.method IN ('UPI', 'bank_transfer') "
              "AND p.paid_on >= (SELECT MIN(txn_date) FROM v_bank_statement) "
              "AND p.reference NOT IN (SELECT utr FROM v_bank_statement WHERE utr IS NOT NULL) ORDER BY 1")
_OVERDUE_GROUPED = ("SELECT student_id, student_name AS name, student_phone AS phone, batch_code, MIN(due_date) AS due_date, "
                    "SUM(balance) AS amount_due FROM v_instalments WHERE status IN ('overdue', 'partial') "
                    "AND due_date < '{today}' GROUP BY student_id ORDER BY amount_due DESC")
EXTRA: list[dict] = [
    dict(id="x_due_total", topic="Fees", q="What is the total EMI amount due this week?",
         alt=["total emi amount due between two dates", "how much emi is due this week in total", "total amount due this week",
              "total emi amount due between dates in october"],
         sql="SELECT COUNT(*) AS emis_due, SUM(balance) AS total_due FROM v_instalments WHERE status != 'paid' "
             "AND due_date BETWEEN '{today}' AND '{week_end}'",
         why="Number and total of unpaid EMIs due this week."),
    dict(id="x_overdue_total", topic="Fees", q="What is the total overdue EMI amount right now?",
         alt=["total overdue amount", "how much is overdue in total", "overdue fees total"],
         sql="SELECT COUNT(*) AS overdue_emis, SUM(balance) AS total_overdue, "
             "SUM(CASE WHEN status = 'overdue' THEN balance ELSE 0 END) AS fully_unpaid_overdue, "
             "SUM(CASE WHEN status = 'partial' THEN balance ELSE 0 END) AS partly_paid_balance FROM v_instalments "
             "WHERE status IN ('overdue', 'partial') AND due_date < '{today}'",
         why="Total overdue balance, split into unpaid EMIs and the rest of partly paid EMIs."),
    dict(id="x_at_risk", topic="Students", q="Who is at risk of dropping out, and why?",
         alt=["students at risk", "dropout risk", "who might drop out", "students falling behind with reasons"],
         sql=_RISK, trainer=_RISK_T,
         why="Students with low attendance, recent absences, overdue EMIs or missing assignments, with the reasons."),
    dict(id="x_seat_check", topic="Students", q="Can I add a 9th online student to the Python Evening batch?",
         alt=["can b02 take one more online student", "is there an online seat in python evening",
              "add one more online student to a batch"],
         sql="SELECT code, short_name, online_seat_limit, online_students, online_seat_limit - online_students AS online_seats_left, "
             "CASE WHEN online_students >= online_seat_limit THEN 'No: the online seat limit is reached, the batch is full' "
             "ELSE 'Yes: ' || (online_seat_limit - online_students) || ' online seat(s) left' END AS can_add_online "
             "FROM v_batches WHERE short_name = 'Python Evening'",
         trainer="SELECT code, short_name, online_seat_limit, online_students, online_seat_limit - online_students "
                 "AS online_seats_left FROM t_my_batches WHERE short_name = 'Python Evening'",
         why="Online seats used and left in Python Evening (B02)."),
    dict(id="x_batch_low_att", topic="Attendance & academics",
         q="Which students in the Python Evening batch have attendance below 75%?",
         alt=["low attendance in one batch", "b02 students below 75 percent attendance"],
         sql="SELECT student_name, batch_code, classes_held, classes_attended, attendance_pct FROM v_certificate_eligibility "
             "WHERE batch_code = 'B02' AND enrollment_status = 'active' AND attendance_pct < 75 ORDER BY attendance_pct",
         trainer="SELECT student_name, batch_code, COUNT(*) AS classes_held, SUM(status != 'absent') AS attended, "
                 "ROUND(100.0 * SUM(status != 'absent') / COUNT(*), 1) AS attendance_pct FROM t_my_attendance "
                 "WHERE batch_code = 'B02' GROUP BY student_id HAVING attendance_pct < 75 ORDER BY attendance_pct",
         why="Python Evening (B02) students under 75% attendance."),
    dict(id="x_missed3", topic="Attendance & academics", q="Which students in Java Morning have missed 3 or more classes?",
         alt=["who missed three classes in b01", "students with 3 absences in a batch"],
         sql="SELECT student_name, batch_code, SUM(status = 'absent') AS classes_missed, COUNT(*) AS classes_held "
             "FROM v_attendance WHERE batch_code = 'B01' GROUP BY student_id HAVING classes_missed >= 3 "
             "ORDER BY classes_missed DESC",
         trainer="SELECT student_name, batch_code, SUM(status = 'absent') AS classes_missed, COUNT(*) AS classes_held "
                 "FROM t_my_attendance WHERE batch_code = 'B01' GROUP BY student_id HAVING classes_missed >= 3 "
                 "ORDER BY classes_missed DESC",
         why="Java Morning (B01) students with 3 or more absences."),
    dict(id="x_roster", topic="Students", q="Who is enrolled in the new Java Evening batch?",
         alt=["students in batch b07", "list the students of a batch", "who has joined the new batch"],
         sql="SELECT student_name, learning_mode, enrolled_on FROM v_enrollments WHERE batch_code = 'B07' "
             "AND status = 'active' ORDER BY student_name",
         why="Students enrolled in Java Evening (B07), which starts soon."),
    dict(id="x_my_week", topic="Today & timetable", q="What is my teaching schedule this week?",
         alt=["my classes this week", "my lectures this week", "where do I teach this week"],
         sql="", trainer="SELECT class_date, day_name, start_time, end_time, batch_code, course_name, room_name "
                         "FROM t_my_schedule WHERE class_date BETWEEN '{today}' AND '{week_end}' ORDER BY class_date, start_time",
         why="Your classes from today to the same day next week, with rooms."),
    dict(id="x_cert_status", topic="Reminders & certificates", q="Which students are eligible for a completion certificate?",
         alt=["certificate eligible students accounts", "who qualifies for the certificate"],
         sql="SELECT student_name, batch_code, course_name, fees, eligible, certificate_no FROM v_certificate_status "
             "WHERE batch_status = 'completed' ORDER BY eligible DESC, student_name",
         why="Finished batches: who qualifies for a certificate (attendance at least 75% and fees paid)."),
    dict(id="x_reconcile", topic="Fees", q="Reconcile the bank statement against expected instalments.",
         alt=["reconcile last month's bank statement", "bank reconciliation", "match bank credits with payments"],
         sql=_RECONCILE,
         why="Bank lines that need a look (duplicates, unmatched, amount differences, interest) and payments missing "
             "from the bank."),
    dict(id="x_parent_msg", topic="Reminders & certificates",
         q="Draft a WhatsApp message to Vivek Joshi's parent about his recent absences.",
         alt=["message the parent about absences", "tell the parents their child is absent", "whatsapp the guardian"],
         sql="SELECT s.id AS student_id, s.name, s.parent_name, s.guardian_phone AS phone, e.batch_code, "
             "SUM(a.status = 'absent') AS absences FROM v_students s JOIN v_enrollments e ON e.student_id = s.id "
             "AND e.status = 'active' JOIN v_attendance a ON a.student_id = s.id AND a.batch_id = e.batch_id "
             "AND a.session_date >= date('{today}', '-21 days') WHERE s.name LIKE '%Vivek Joshi%' GROUP BY s.id, e.batch_id",
         plan={"type": "action", "action_type": "send_whatsapp", "title": "Absence note to parent",
               "message_template": "Namaste {parent_name}, this is {institute}. {name} has missed {absences} classes in "
                                   "{batch_code} over the last three weeks. Is everything all right? Recordings are on the "
                                   "portal, and we are happy to help {name} catch up. Thank you!"},
         why="A message to Vivek Joshi's parent about his absences in the last three weeks."),
    dict(id="x_remind_owing", topic="Reminders & certificates", q="Send a reminder to everyone who owes money right now.",
         alt=["remind all defaulters", "whatsapp everyone with overdue fees", "remind everyone who has not paid"],
         sql=_OVERDUE_GROUPED,
         plan={"type": "action", "action_type": "send_whatsapp", "title": "Overdue EMI reminder",
               "message_template": "Hi {name}, a gentle reminder from {institute}: ₹{amount_due} of your fees for "
                                   "{batch_code} has been due since {due_date}. Please pay at your earliest convenience, or "
                                   "reply here if you need help. Thank you!"},
         why="Everyone with an overdue EMI, one message each with the total overdue."),
    dict(id="x_mark_all_paid", topic="Fees", q="Mark all of Rohan Gupta's unpaid EMIs as paid.",
         alt=["mark a student's emis paid", "set his fees as paid"],
         sql="SELECT id AS instalment_id, student_name, instalment_no, due_date, balance FROM v_instalments "
             "WHERE student_name LIKE '%Rohan Gupta%' AND status != 'paid' ORDER BY due_date",
         plan={"type": "action", "action_type": "mark_instalment_paid", "title": "Mark Rohan Gupta's EMIs paid"},
         why="Rohan Gupta's unpaid instalments, to be marked paid after approval."),
    dict(id="x_bank_payment", topic="Fees", q="Record the ₹11,000 UPI credit from Suresh Gupta as Rohan Gupta's August EMI.",
         alt=["mark the money from a parent as the student's emi", "record a parent's upi payment"],
         sql="SELECT txn_date, description, credit, utr FROM v_bank_statement WHERE credit = 11000 "
             "AND lower(description) LIKE '%suresh gupta%'",
         plan={"type": "action", "action_type": "record_payment", "student_name": "Rohan Gupta", "instalment_no": 2,
               "amount": 11000, "method": "UPI"},
         why="Record the parent's UPI credit against Rohan Gupta's EMI 2 (the reference is matched from the bank)."),
]
ALL = BANK + EXTRA

# What people usually ask next. Sentences in ACTION_NEXT prepare an approval preview (role-checked).
REMIND_OVERDUE = "Send a WhatsApp reminder for overdue EMIs"
REMIND_DUE = "Send a WhatsApp reminder for EMIs due this week"
ISSUE_CERTS = "Issue certificates to eligible students"
ACTION_NEXT = {REMIND_OVERDUE: "send_whatsapp", REMIND_DUE: "send_whatsapp", ISSUE_CERTS: "issue_certificate"}
NEXT = {
    "fee_structure": ["What is the fee for Java Full Stack and how many EMIs?", "Which EMIs are due this week?",
                      "How much fee is still pending in each batch?"],
    "fee_one_course": ["What is the total fee structure?", "Which EMIs are due this week?"],
    "emi_due_week": [REMIND_DUE, "Who has overdue fees?", "How much fee did we collect this month?"],
    "overdue": [REMIND_OVERDUE, "How much fee is still pending in each batch?", "Which bank credits don't match any payment?"],
    "pending_by_batch": ["Who has overdue fees?", "Which EMIs are due this week?"],
    "collected_month": ["Which bank credits don't match any payment?", "Who has overdue fees?"],
    "student_fee": ["Who has overdue fees?", REMIND_OVERDUE],
    "bank_unmatched": ["How much fee did we collect this month?", "Who has overdue fees?"],
    "today_lectures": ["Which trainer is taking today's lectures?", "Which classes are affected by trainer leave?",
                       "What is tomorrow's timetable?"],
    "today_trainers": ["Which classes are affected by trainer leave?", "What is tomorrow's timetable?",
                       "Which room is free today at 6 pm?"],
    "today_course_time": ["What are today's lectures?", "What is tomorrow's timetable?"],
    "tomorrow": ["Show all lectures in the next 7 days", "Which classes are affected by trainer leave?"],
    "week_lectures": ["Is any trainer double-booked this week?", "Which classes are affected by trainer leave?"],
    "next_week": ["Is any trainer double-booked this week?", "Which classes are affected by trainer leave?"],
    "trainer_batches": ["Which trainer teaches which batch?", "What are today's lectures?"],
    "who_teaches": ["Which trainer is taking today's lectures?", "How many students are in each batch?"],
    "leave_affected": ["Is any trainer double-booked this week?", "Show next week's timetable"],
    "double_booked": ["Which classes are affected by trainer leave?", "Show next week's timetable"],
    "free_room": ["What are today's lectures?"],
    "students_per_batch": ["How many online seats are left in each batch?", "Which students have attendance below 75%?"],
    "online_seats": ["How many students are in each batch?", "Which new batches are starting soon?"],
    "student_details": ["Which students have attendance below 75%?"],
    "upcoming_batches": ["How many online seats are left in each batch?"],
    "yesterday_attendance": ["Which students have attendance below 75%?", "Who was absent in the last class of B02?"],
    "last_class_absent": ["Which students have attendance below 75%?", "What was yesterday's attendance?"],
    "low_attendance": [D01, "Who is eligible for a certificate?"],
    "missing_submission": ["Which classes are missing a recording?", "Which students have attendance below 75%?"],
    "missing_recordings": ["Who has not submitted the latest assignment in B01?"],
    "certificates": [ISSUE_CERTS, "Which students have attendance below 75%?"],
    "d01_reminder": ["Who has overdue fees?", "Which students have attendance below 75%?"],
}
_BY_Q = {e["q"]: e for e in BANK}


# ---------------------------------------------------------------- dates
def dates() -> dict:
    t = config.today()
    monday = t - timedelta(days=t.weekday())
    return {"today": t.isoformat(), "tomorrow": (t + timedelta(days=1)).isoformat(),
            "yesterday": (t - timedelta(days=1)).isoformat(), "week_end": (t + timedelta(days=6)).isoformat(),
            "next_mon": (monday + timedelta(days=7)).isoformat(), "next_sun": (monday + timedelta(days=13)).isoformat(),
            "month_start": t.replace(day=1).isoformat()}


def _fill(text: str, d: dict) -> str:
    return re.sub(r"\{(today|tomorrow|yesterday|week_end|next_mon|next_sun|month_start)\}", lambda m: d[m.group(1)], text)


# ---------------------------------------------------------------- which entries a role can run
_OBJ = re.compile(r"\b((?:v|t_my)_[a-z_]+)\b")


def sql_for(entry: dict, role: str) -> str | None:
    """The entry's SQL for this role, or None if the role may not read what it needs."""
    sql = entry.get("trainer") if role == "trainer" else entry["sql"]
    if not sql:
        return None
    needed = set(_OBJ.findall(sql))
    if not needed <= rbac.allowed_objects(role):
        return None
    if entry.get("plan") and not rbac.can_do(role, entry["plan"]["action_type"]):
        return None
    return sql


def for_role(role: str) -> list[dict]:
    """The questions this role can ask, for the question list in the app."""
    d = dates()
    return [{"id": e["id"], "topic": e["topic"], "question": e["q"], "explanation": _fill(e["why"], d)}
            for e in BANK if sql_for(e, role)]


def plan_for(entry: dict, role: str, institute: str = "the institute") -> dict | None:
    """The copilot's answer plan (the JSON the LLM would return) for this entry and role."""
    sql = sql_for(entry, role)
    if not sql:
        return None
    d = dates()
    plan = {"type": "query", "sql": _fill(sql, d), "explanation": _fill(entry["why"], d)}
    if entry.get("plan"):
        plan.update(entry["plan"])
        if "message_template" in plan:
            plan["message_template"] = plan["message_template"].replace("{institute}", institute)
    return plan


# ---------------------------------------------------------------- finding the closest questions
SYNONYMS = {    # English, Hinglish, Hindi and Marathi words (voice questions arrive in Devanagari)
    "class": "lecture lectures lec lecs class classes session sessions period periods "
             "लेक्चर लेक्चर्स क्लास कक्षा तास तासिका वर्ग",
    "trainer": "trainer trainers sir sirs madam maam mam teacher teachers faculty mentor tutor "
               "सर मॅडम मैडम शिक्षक टीचर फैकल्टी",
    "fee": "fee fees फीस फी शुल्क",
    "structure": "structure स्ट्रक्चर रचना",
    "emi": "emi emis instalment instalments installment installments kisht ईएमआई हप्ता हप्ते किश्त",
    "today": "today todays aaj आज आजचे आजच्या आजचा",
    "tomorrow": "tomorrow tomorrows kal कल उद्या उद्याचे उद्याच्या",
    "yesterday": "yesterday yesterdays काल",
    "schedule": "timetable timetables schedule schedules routine टाइमटेबल वेळापत्रक शेड्यूल",
    "time": "time timing timings when baje समय वेळ बजे वाजता",
    "absent": "absent absentees absence missed miss गैरहाजिर अनुपस्थित गैरहजर",
    "student": "student students learner learners विद्यार्थी छात्र स्टूडेंट",
    "week": "week weekly hafte hafta हफ्ते हफ्ता आठवडा आठवड्यात",
    "paid": "paid pay payment payments collected collection received भुगतान जमा",
    "overdue": "overdue defaulter defaulters late बकाया थकबाकी",
    "room": "room rooms lab labs classroom classrooms hall रूम कमरा खोली",
}
_SYN = {w: k for k, words in SYNONYMS.items() for w in words.split()}
STOP = set("""a an the is are was were be of for in on at to me my our we us you your i it its this that these those
what which who whom whose how much many do does did show give tell list get find please all any some there here and or
with by from about can could would should will kya hai hain ka ke ki ko se ye wo kaun kaunse kaunsa batao bata dikhao
है हैं के की का को क्या कौन कौनसे कौनसा कौनसी कौनसे से में कितने कितनी कितना मेरे मेरा मेरी बताओ बताइए दिखाओ
आहे आहेत कोणते कोणता कोणती कोण चे ची चा च्या ला ना किती सांगा दाखवा माझे माझी""".split())
_TOKEN = re.compile(r"[^\s.,!?;:'\"()\[\]{}/\\|\-–—]+")


def _words(text: str) -> set[str]:
    out = set()
    for w in _TOKEN.findall((text or "").lower().replace("'s", "").replace("’s", "")):
        if w in STOP or len(w) < 2:
            continue
        out.add(_SYN.get(w, w))
    return out


_INDEX = [(e, [_words(e["q"])] + [_words(a) for a in e.get("alt", [])]) for e in ALL]


def _scores(question: str, entry_words: list[set[str]]) -> tuple[float, float]:
    """(similarity, coverage) against the entry's best phrasing. coverage = share of the question's words found."""
    q = _words(question)
    best = (0.0, 0.0)
    for w in entry_words:
        if q and w:
            both = len(q & w)
            best = max(best, (both / math.sqrt(len(q) * len(w)), both / len(q)))
    return best


def score(question: str, entry_words: list[set[str]]) -> float:
    return _scores(question, entry_words)[0]


def closest(question: str, role: str, k: int = 4) -> list[tuple[float, dict]]:
    """The k entries closest to the question that this role can run, best first."""
    hits = [(score(question, words), e) for e, words in _INDEX if sql_for(e, role)]
    hits = [h for h in hits if h[0] > 0]
    hits.sort(key=lambda h: -h[0])
    return hits[:k]


def prompt_examples(question: str, role: str, institute: str, k: int = 3) -> str:
    """Worked examples for the LLM prompt: the closest checked questions and their answers."""
    lines = []
    for _, e in closest(question, role, k):
        lines.append(f"User: {e['q']}\n{json.dumps(plan_for(e, role, institute), ensure_ascii=False)}")
    return "\n".join(lines)


def match(question: str, role: str, threshold: float = 0.7) -> dict | None:
    """For offline mode: the entry this question clearly is (or None). Every important word of the question
    must be in the entry, so "fee details of my students" never matches "student details"."""
    best, best_entry = (0.0, 0.0), None
    for e, words in _INDEX:
        if sql_for(e, role):
            sc = _scores(question, words)
            if sc > best:
                best, best_entry = sc, e
    return best_entry if best[0] >= threshold and best[1] >= 0.75 else None


def follow_ups(question: str, role: str, limit: int = 3) -> list[str]:
    """Up to `limit` natural next questions after this one, all of them allowed for this role."""
    entry = match(question, role, threshold=0.6)
    if entry is None:
        best = closest(question, role, 1)
        entry = best[0][1] if best and best[0][0] >= 0.5 else None
    if entry is None:
        return []
    asked = _words(question)
    out = []
    for text in NEXT.get(entry["id"], []):
        if text in ACTION_NEXT:
            ok = rbac.can_do(role, ACTION_NEXT[text])
        else:
            ok = text in _BY_Q and sql_for(_BY_Q[text], role) is not None
        if ok and _words(text) != asked:
            out.append(text)
    return out[:limit]
