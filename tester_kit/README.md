# Tester kit (configured for this backend)

1. Fresh data: stop uvicorn, run `python -m app.import_csv`, start `uvicorn app.main:app --port 8000` again.
2. `pip install requests`, then from this folder: `python run_tests.py`
   To test the live site instead: PowerShell `$env:API_BASE="https://institute-copilot.onrender.com"`,
   Git Bash `export API_BASE=https://institute-copilot.onrender.com`, then run it again.
3. Read `report.md`: fix CRITICAL (attack) failures first, then the rest. Do the listed manual checks.
4. Never click "approve" during a run: the point is that nothing happens without approval.
   `OUTBOX` at the end must say 0 messages sent.

**AI usage:** each question uses about 4,000-5,000 AI tokens. Groq's free tier allows 8,000 tokens a minute and
200,000 a day for gpt-oss-120b, so one full run takes about 20 minutes (the backend waits and retries when told to
slow down) and uses most of a day's quota. To check the logic for free, run it with `LLM_PROVIDER=mock` in `.env`
(restart uvicorn); to test the real AI, use a second API key, not the one the demo needs.

Changes from the organisers' copy: CONFIG paths point at this API (`/api/login`, `/api/copilot/ask`, `/api/outbox`),
and `has()` ignores empty alternatives (in the original, the "₹" check in Q16 becomes an empty string after
normalising, so Q16 always failed for every team).

Status meanings: PASS = auto-checks ok. REVIEW = auto-checks ok but a human must verify the manual check. FAIL = something is wrong.
Auto-checks are keyword based, so also skim `results.csv` for wrong-but-plausible answers.
