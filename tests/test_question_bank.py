"""The 30 checked everyday questions (app/question_bank.py) and the by-date schedule view.
Run:  python -m pytest -q"""
import os
import sys
import tempfile
from pathlib import Path

if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["LLM_PROVIDER"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import actions, config, copilot, db, import_csv, planner, question_bank, rbac, sql_guard  # noqa: E402

SEED = Path(__file__).resolve().parent.parent / "seed_data"
pytestmark = pytest.mark.skipif(not (SEED / "students.csv").exists(), reason="no seed_data folder")

OWNER, COUNSELOR, ACCOUNTS, RAHUL = ("owner@brightpath.example", "pooja@brightpath.example",
                                     "accounts@brightpath.example", "rahul@brightpath.example")


@pytest.fixture(autouse=True)
def brightpath(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "DEMO_TODAY", "2026-10-16")     # a Friday
    monkeypatch.setattr(config, "PLAN_WEEK", "")
    import_csv.import_folder(SEED)
    yield


def user(email):
    conn = db.get_conn()
    try:
        return dict(conn.execute("SELECT id, username, role, display_name, trainer_id FROM users WHERE username=?",
                                 (email,)).fetchone())
    finally:
        conn.close()


def run(email, entry_id):
    u = user(email)
    entry = next(e for e in question_bank.BANK if e["id"] == entry_id)
    plan = question_bank.plan_for(entry, u["role"], "Brightpath Institute")
    return sql_guard.run_query(plan["sql"], u)["rows"]


def test_thirty_unique_questions():
    ids = [e["id"] for e in question_bank.BANK]
    assert len(ids) == 30 and len(set(ids)) == 30
    assert all(e["q"] and e["why"] and e["topic"] for e in question_bank.BANK)


@pytest.mark.parametrize("email", [OWNER, COUNSELOR, ACCOUNTS, RAHUL])
def test_every_question_runs_for_every_role_that_may_ask_it(email):
    u = user(email)
    runnable = 0
    for e in question_bank.BANK:
        plan = question_bank.plan_for(e, u["role"], "Brightpath Institute")
        if plan is None:
            continue
        runnable += 1
        sql_guard.run_query(plan["sql"], u)                  # raises if blocked or broken
    assert runnable == len(question_bank.for_role(u["role"]))
    assert runnable >= 14


def test_trainer_never_gets_fee_questions():
    names = {q["id"] for q in question_bank.for_role("trainer")}
    assert not names & {"fee_structure", "fee_one_course", "emi_due_week", "overdue", "pending_by_batch",
                        "collected_month", "student_fee", "bank_unmatched", "certificates", "d01_reminder"}
    for e in question_bank.BANK:
        sql = question_bank.sql_for(e, "trainer")
        if sql:
            assert set(question_bank._OBJ.findall(sql)) <= rbac.allowed_objects("trainer")


def test_fee_structure():
    rows = run(ACCOUNTS, "fee_structure")
    assert len(rows) == 7
    java = next(r for r in rows if r["course"] == "Java Full Stack")
    assert (java["total_fee"], java["emis"], java["per_emi"]) == (60000, 5, 12000)


def test_todays_lectures_and_who_takes_them():
    rows = run(COUNSELOR, "today_lectures")                                       # Friday 16 Oct
    assert [(r["start_time"], r["batch_code"], r["trainer_name"], r["room_name"]) for r in rows] == [
        ("09:00", "B01", "Rahul Deshmukh", "Lab A"), ("18:00", "B02", "Rahul Deshmukh", "Seminar Hall")]
    who = run(OWNER, "today_trainers")
    assert len(who) == 1 and who[0]["trainer_name"] == "Rahul Deshmukh" and who[0]["lectures"] == 2
    java = run(OWNER, "today_course_time")
    assert [(r["start_time"], r["end_time"]) for r in java] == [("09:00", "11:00")]
    mine = run(RAHUL, "today_lectures")
    assert [r["batch_code"] for r in mine] == ["B01", "B02"] and "trainer_name" not in mine[0]


def test_tomorrow_and_leave():
    sat = run(OWNER, "tomorrow")
    assert {r["batch_code"] for r in sat} == {"B03", "B04"} and {r["trainer_name"] for r in sat} == {"Meera Iyer"}
    leave = run(COUNSELOR, "leave_affected")                                     # Sunita: family function Tue 20
    assert [(r["class_date"], r["batch_code"], r["trainer_name"]) for r in leave] == [("2026-10-20", "B05", "Sunita Joshi")]


def test_schedule_shows_the_substitute_after_replanning():
    conn = db.get_conn()
    owner = user(OWNER)
    out = planner.propose_trainer_unavailable(conn, owner, 3, ["Tue"], "family function")
    actions.approve(conn, owner, out["action"]["id"])
    rows = sql_guard.run_query("SELECT batch_code, trainer_name, trainer_on_leave FROM v_schedule "
                               "WHERE class_date = '2026-10-20' AND batch_code = 'B05'", owner)["rows"]
    assert rows == [{"batch_code": "B05", "trainer_name": "Vikram Rao", "trainer_on_leave": 0}]
    assert run(COUNSELOR, "leave_affected") == []
    vikram = {"id": 0, "role": "trainer", "trainer_id": 6}
    mine = sql_guard.run_query("SELECT class_date, batch_code FROM t_my_schedule WHERE class_date = '2026-10-20'", vikram)
    assert mine["rows"] == [{"class_date": "2026-10-20", "batch_code": "B05"}]


def test_accounts_cannot_read_the_schedule():
    with pytest.raises(sql_guard.GuardError) as e:
        sql_guard.run_query("SELECT * FROM v_schedule", user(ACCOUNTS))
    assert e.value.code == "forbidden"


@pytest.mark.parametrize("email,question,first_col,expect", [
    (COUNSELOR, "What is the total fee structure?", "course_code", 7),
    (ACCOUNTS, "fee structure", "course_code", 7),
    (COUNSELOR, "what is the todays lec time", "start_time", 2),
    (COUNSELOR, "todays lec timetable", "start_time", 2),
    (OWNER, "which sir is alloted today", "trainer_name", 1),
    (OWNER, "aaj kaunse sir ka lecture hai", "trainer_name", 1),
    (OWNER, "kal ke lecture", "start_time", 2),
    (RAHUL, "what are my lectures today", "start_time", 2),
    (OWNER, "which room is free today at 6 pm", "room", 3),
    (COUNSELOR, "Which trainer teaches which batch?", "code", 7),
])
def test_offline_copilot_answers_everyday_questions(email, question, first_col, expect):
    r = copilot.ask(user(email), question)
    assert r["type"] == "answer", r["answer"]
    assert r["columns"][0] == first_col and r["row_count"] == expect


@pytest.mark.parametrize("email,question", [
    (RAHUL, "What is the total fee structure?"),
    (RAHUL, "Show me the fee details of my students"),
    (ACCOUNTS, "What are today's lectures?"),
])
def test_questions_outside_the_role_are_still_refused(email, question):
    r = copilot.ask(user(email), question)
    assert r["type"] in ("blocked", "refusal") and not r["rows"]


def test_prompt_has_glossary_and_closest_checked_examples():
    conn = db.get_conn()
    u = user(COUNSELOR)
    p = copilot.build_prompt(conn, u, copilot.context_facts(conn, u), "todays lec timetable which sir is alloted")
    assert "sir / ma'am" in p and "aaj = today" in p and "fee structure = course fees" in p
    assert "User: What are today's lectures?" in p and "class_date = '2026-10-16'" in p
    t = user(RAHUL)
    p = copilot.build_prompt(conn, t, copilot.context_facts(conn, t), "what is the fee structure")
    assert "v_courses_with_fee" not in p and "v_schedule" not in p and "t_my_schedule" in p


def test_question_list_endpoint():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        counts = {}
        for email in (OWNER, COUNSELOR, ACCOUNTS, RAHUL):
            tok = client.post("/api/login", json={"username": email, "password": "demo123"}).json()["token"]
            items = client.get("/api/copilot/questions", headers={"Authorization": f"Bearer {tok}"}).json()["items"]
            counts[email] = len(items)
            assert all({"id", "topic", "question", "explanation"} <= set(i) for i in items)
        assert counts[OWNER] == 30 and counts[COUNSELOR] == 27 and counts[ACCOUNTS] == 14 and counts[RAHUL] == 14
