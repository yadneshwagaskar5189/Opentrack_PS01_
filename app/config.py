"""Settings, read from environment variables or a .env file in the project root."""
import os
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DB_PATH = os.getenv("DB_PATH", str(ROOT / "institute.db"))

# LLM: any OpenAI-compatible chat API (Groq, Gemini, OpenRouter, OpenCode, Ollama...)
# LLM_PROVIDER=mock runs an offline rule-based stand-in (for frontend dev / no internet).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compat")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))

QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "3000"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))

# Institute-wide limit of simultaneous online classes (e.g. number of Zoom licences)
ONLINE_LICENCES = int(os.getenv("ONLINE_LICENCES", "2"))

CORS_ORIGINS = [o.strip() for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",") if o.strip()]

# Freeze "today" for a stable demo, e.g. DEMO_TODAY=2026-10-16. Empty = real date.
DEMO_TODAY = os.getenv("DEMO_TODAY", "").strip()

# Folder with the institute's CSV files (see app/import_csv.py). If it exists, the database
# is built from it on first start and on "Reset demo data"; otherwise sample data is generated.
SEED_DIR = os.getenv("SEED_DIR", str(ROOT / "seed_data"))

# Which week the timetable shows and plans: any date inside it, e.g. PLAN_WEEK=2026-10-19.
# Empty = this week from Monday to Thursday, next week from Friday (you plan next week on Friday).
PLAN_WEEK = os.getenv("PLAN_WEEK", "").strip()


def today() -> date:
    return date.fromisoformat(DEMO_TODAY) if DEMO_TODAY else date.today()


def week_bounds(d: date | None = None) -> tuple[date, date]:
    """Monday and Sunday of the week containing d."""
    d = d or today()
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def plan_week() -> tuple[date, date]:
    """Monday and Sunday of the week the timetable shows and re-plans."""
    if PLAN_WEEK:
        return week_bounds(date.fromisoformat(PLAN_WEEK))
    t = today()
    return week_bounds(t + timedelta(days=7) if t.weekday() >= 4 else t)


def due_window() -> tuple[date, date]:
    """'This week' for fees and deadlines: today and the next 6 days."""
    t = today()
    return t, t + timedelta(days=6)
