"""FastAPI app. Run:  uvicorn app.main:app --reload --port 8000
Interactive API docs: http://localhost:8000/docs
"""
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import (actions, audit, auth, config, copilot, dashboard, db, import_csv, planner, rbac, reconcile,
               risk, sql_guard)


def _database_outdated() -> bool:
    """True if the database is missing or was made by an older version of this backend."""
    if not Path(config.DB_PATH).exists():
        return True
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_settings'").fetchone() is None
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _database_outdated():
        # first run (or an old database): load seed_data/*.csv, or generate sample data if there is none
        print("Building the database:", import_csv.build_demo_database().get("source", "sample data"))
    yield


app = FastAPI(title="Institute Operations Copilot API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- auth
def get_db():
    conn = db.get_conn()
    try:
        yield conn
    finally:
        conn.close()


def current_user(authorization: Optional[str] = Header(default=None), conn=Depends(get_db)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing token. Log in and send 'Authorization: Bearer <token>'.")
    user = auth.user_from_token(conn, authorization.split(" ", 1)[1].strip())
    if user is None:
        raise HTTPException(401, "Invalid or expired token. Please log in again.")
    return user


def require(*roles):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"Your role ({user['role']}) cannot do this.")
        return user
    return dep


def _http(e: Exception):
    status = getattr(e, "status", 400)
    raise HTTPException(status, getattr(e, "message", str(e)))


# ---------------------------------------------------------------- models
class LoginIn(BaseModel):
    username: str
    password: str


class AskIn(BaseModel):
    question: str = Field(..., examples=["Which students in the Java batch have missed three or more classes "
                                         "and have an EMI due this week? Draft a WhatsApp reminder for each."])
    history: list[dict[str, Any]] = Field(default_factory=list,
                                          description="Optional last turns: [{question, sql}] for follow-ups")


class ApproveIn(BaseModel):
    edits: dict[str, Any] = Field(default_factory=dict, examples=[{
        "items": {"0": {"message": "Edited text for the first recipient"}}, "exclude": [2]}])


class RejectIn(BaseModel):
    reason: Optional[str] = None


class UnavailableIn(BaseModel):
    trainer_id: int
    days: list[Any] = Field(..., examples=[["Wed"], [2], ["2026-10-21"]])
    reason: Optional[str] = "unavailable"


class NewBatchIn(BaseModel):
    code: str = Field(..., examples=["NB01"])
    course_code: str = Field(..., examples=["C07"])
    trainer_id: int = Field(..., examples=[6])
    name: Optional[str] = None
    mode: str = Field("hybrid", examples=["classroom", "online", "hybrid"])
    time_window: Optional[str] = Field(None, examples=["morning", "afternoon", "evening", "weekend"])
    preferred_days: Optional[list[Any]] = Field(None, examples=[["Tue", "Thu"]])
    start_time: Optional[str] = Field(None, examples=["17:00"])
    session_hours: Optional[float] = Field(None, examples=[2])
    sessions_per_week: Optional[int] = None
    classroom_size: Optional[int] = None
    online_size: Optional[int] = None
    online_seat_limit: Optional[int] = None
    start_date: Optional[str] = None


# ---------------------------------------------------------------- routes
@app.get("/api/health")
def health(conn=Depends(get_db)):
    ws, we = config.plan_week()
    return {"ok": True, "llm": "mock" if config.LLM_PROVIDER == "mock" else config.LLM_MODEL,
            "llm_key_set": bool(config.LLM_API_KEY), "today": config.today().isoformat(),
            "institute": db.institute_name(conn), "plan_week": [ws.isoformat(), we.isoformat()]}


@app.post("/api/login")
def login(body: LoginIn, conn=Depends(get_db)):
    result = auth.login(conn, body.username, body.password)
    if result is None:
        audit.log(conn, None, "login_failed", outcome="failed", detail={"username": body.username})
        raise HTTPException(401, "Wrong username or password.")
    audit.log(conn, result["user"], "login", outcome="ok")
    return result


@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(default=None), conn=Depends(get_db)):
    if authorization and authorization.lower().startswith("bearer "):
        auth.logout(conn, authorization.split(" ", 1)[1].strip())
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    role = rbac.ROLES[user["role"]]
    return {"user": user, "role_label": role["label"], "can_see_fees": role["can_see_fees"],
            "allowed_actions": role["actions"], "restrictions": rbac.ROLE_RESTRICTIONS[user["role"]],
            "data_views": role["objects"]}


@app.post("/api/copilot/ask")
def ask(body: AskIn, user=Depends(current_user)):
    return copilot.ask(user, body.question, body.history)


class TrySqlIn(BaseModel):
    sql: str = Field(..., examples=["SELECT * FROM instalments"])


@app.post("/api/security/try-sql")
def try_sql(body: TrySqlIn, user=Depends(current_user), conn=Depends(get_db)):
    """Run raw SQL through the database guard with no AI involved. Proves the security
    does not depend on the AI: anything outside the user's role is refused by the database."""
    try:
        r = sql_guard.run_query(body.sql, user)
    except sql_guard.GuardError as e:
        aid = audit.log(conn, user, "query_blocked", question="[direct SQL test, no AI]", sql=body.sql,
                        outcome="blocked_by_guard", detail={"blocked": e.blocked, "code": e.code, "message": e.message})
        return {"type": "blocked", "answer": e.message, "sql": body.sql, "explanation": "Raw SQL sent straight to the database guard.",
                "columns": [], "rows": [], "row_count": 0, "truncated": False, "objects_read": [],
                "blocked": e.blocked or [e.code], "audit_id": aid}
    aid = audit.log(conn, user, "query_ok", question="[direct SQL test, no AI]", sql=r["sql"], outcome="allowed",
                    detail={"rows": r["row_count"], "objects_read": r["objects_read"]})
    return {"type": "answer", "answer": f"Allowed for your role: {r['row_count']} row(s).", "sql": r["sql"],
            "explanation": "Raw SQL sent straight to the database guard.", "columns": r["columns"],
            "rows": r["rows"][:50], "row_count": r["row_count"], "truncated": r["truncated"] or r["row_count"] > 50,
            "objects_read": r["objects_read"], "blocked": [], "audit_id": aid}


@app.get("/api/actions")
def list_actions(status: Optional[str] = Query(None, examples=["pending"]), user=Depends(current_user),
                 conn=Depends(get_db)):
    return {"items": actions.list_actions(conn, user, status)}


@app.get("/api/actions/{action_id}")
def get_action(action_id: int, user=Depends(current_user), conn=Depends(get_db)):
    try:
        return actions.get(conn, user, action_id)
    except actions.ActionError as e:
        _http(e)


@app.post("/api/actions/{action_id}/approve")
def approve(action_id: int, body: ApproveIn | None = None, user=Depends(current_user), conn=Depends(get_db)):
    try:
        return actions.approve(conn, user, action_id, (body.edits if body else {}))
    except actions.ActionError as e:
        _http(e)


@app.post("/api/actions/{action_id}/reject")
def reject(action_id: int, body: RejectIn | None = None, user=Depends(current_user), conn=Depends(get_db)):
    try:
        return actions.reject(conn, user, action_id, body.reason if body else None)
    except actions.ActionError as e:
        _http(e)


@app.get("/api/audit")
def get_audit(limit: int = 100, event_type: Optional[str] = None, user=Depends(current_user), conn=Depends(get_db)):
    return {"items": audit.list_entries(conn, user, limit, event_type)}


@app.get("/api/timetable")
def get_timetable(user=Depends(current_user), conn=Depends(get_db)):
    try:
        return planner.current(conn, user)
    except planner.PlannerError as e:
        _http(e)


@app.post("/api/timetable/fix-clashes")
def fix_clashes(user=Depends(require("owner")), conn=Depends(get_db)):
    try:
        return planner.propose_fix(conn, user)
    except (planner.PlannerError, actions.ActionError) as e:
        _http(e)


@app.post("/api/timetable/trainer-unavailable")
def trainer_unavailable(body: UnavailableIn, user=Depends(require("owner")), conn=Depends(get_db)):
    try:
        return planner.propose_trainer_unavailable(conn, user, body.trainer_id, body.days, body.reason)
    except (planner.PlannerError, actions.ActionError) as e:
        _http(e)


@app.get("/api/timetable/requests")
def timetable_requests(user=Depends(require("owner")), conn=Depends(get_db)):
    """New-batch requests from the data (e.g. NB01), ready to pre-fill 'Add a batch'."""
    return {"items": planner.batch_requests(conn)}


@app.post("/api/timetable/add-batch")
def add_batch(body: NewBatchIn, user=Depends(require("owner")), conn=Depends(get_db)):
    spec = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return planner.propose_new_batch(conn, user, spec)
    except (planner.PlannerError, actions.ActionError) as e:
        _http(e)


@app.get("/api/students/at-risk")
def students_at_risk(level: str = Query("medium", pattern="^(medium|high)$"), user=Depends(current_user),
                     conn=Depends(get_db)):
    try:
        return risk.at_risk(conn, user, level)
    except risk.RiskError as e:
        _http(e)


@app.get("/api/fees/reconciliation")
def fee_reconciliation(user=Depends(require("owner", "accounts")), conn=Depends(get_db)):
    """Bank statement vs recorded payments: matched, short-paid, duplicate, unmatched, not in bank."""
    audit.log(conn, user, "reconciliation_viewed", outcome="ok")
    return reconcile.reconcile(conn)


@app.get("/api/dashboard")
def get_dashboard(user=Depends(current_user), conn=Depends(get_db)):
    return dashboard.summary(conn, user)


@app.get("/api/{kind}")
def list_kind(kind: str, user=Depends(current_user)):
    if kind not in dashboard.LIST_SQL:
        raise HTTPException(404, "Not found.")
    try:
        return dashboard.list_items(user, kind)
    except sql_guard.GuardError as e:
        raise HTTPException(403, e.message)


@app.post("/api/admin/reset-demo")
def reset_demo(user=Depends(require("owner"))):
    """Rebuild the demo database from seed_data/*.csv (or sample data). Logs everyone out."""
    try:
        return {"ok": True, "summary": import_csv.build_demo_database()}
    except import_csv.ImportError_ as e:
        raise HTTPException(400, str(e))
