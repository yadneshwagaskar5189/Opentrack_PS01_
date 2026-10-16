"""Completion certificates: propose -> a person approves -> numbered certificate + printable page.

Rule: the batch has finished, attendance >= 75% AND fees fully paid (v_certificate_eligibility). It is checked
when the preview is made and again at approval, so a certificate is never issued to someone who
stopped qualifying in between. Each certificate gets a number with a random part, so numbers
can't be guessed; anyone holding the number can verify it at /api/certificates/<number>.
"""
import html
import re
import secrets
from datetime import date

from . import config, db, rbac
from .actions import ActionError, _create, _first

NUMBER = re.compile(r"^[A-Z]{1,6}-\d{4}-\d{4,6}-[0-9A-F]{4}$")


def _prefix(conn) -> str:
    words = re.findall(r"[A-Za-z]+", db.institute_name(conn))
    return ("".join(w[0] for w in words[:4]) or "IC").upper()


def _eligibility(conn, enrollment_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM v_certificate_eligibility WHERE enrollment_id=?", (enrollment_id,)).fetchone()
    return dict(row) if row else None


def propose(conn, user: dict, rows: list[dict], title: str | None = None) -> dict:
    if not rbac.can_do(user["role"], "issue_certificate"):
        raise ActionError(f"Your role ({user['role']}) cannot issue certificates.", 403)
    items, not_ok, seen = [], [], set()
    for r in rows:
        eid = _first(r, "enrollment_id")
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        e = _eligibility(conn, int(eid))
        if not e:
            continue
        if e["certificate_no"]:
            not_ok.append(f"{e['student_name']} ({e['batch_code']}): already has {e['certificate_no']}")
        elif e["batch_status"] != "completed":
            not_ok.append(f"{e['student_name']} ({e['batch_code']}): the course hasn't finished yet")
        elif not e["eligible"]:
            not_ok.append(f"{e['student_name']} ({e['batch_code']}): {e['reason'].lower()}")
        else:
            course = conn.execute("SELECT c.name FROM batches b JOIN courses c ON c.id=b.course_id WHERE b.id=?",
                                  (e["batch_id"],)).fetchone()[0]
            items.append({"enrollment_id": e["enrollment_id"], "student_name": e["student_name"],
                          "batch_code": e["batch_code"], "course": course, "attendance_pct": e["attendance_pct"]})
    if not items:
        raise ActionError("No eligible students without a certificate were found." +
                          (" " + "; ".join(not_ok[:5]) + "." if not_ok else ""), 404)
    preview = {"summary": f"Issue {len(items)} completion certificate(s). Each gets a number and a printable page.",
               "fields": [["Certificates", str(len(items))], ["Rule", "Course finished, attendance at least 75%, fees fully paid"]],
               "table": {"columns": ["Student", "Batch", "Course", "Attendance"],
                         "rows": [[i["student_name"], i["batch_code"], i["course"], f"{i['attendance_pct']}%"] for i in items]},
               "checks": ["Every student meets the rule today; it is checked again when you approve"],
               "warnings": [f"Not included: {x}" for x in not_ok[:5]]}
    return _create(conn, user, "issue_certificate", title or f"Issue {len(items)} certificate(s)",
                   {"items": items, "preview": preview})


def run(conn, a: dict, edits: dict | None = None) -> dict:
    exclude = {int(i) for i in (edits or {}).get("exclude", [])}
    today = config.today()
    prefix, issued, skipped = _prefix(conn), [], []
    for i, item in enumerate(a["payload"]["items"]):
        if i in exclude:
            continue
        e = _eligibility(conn, item["enrollment_id"])
        if not e or not e["eligible"] or e["certificate_no"] or e["batch_status"] != "completed":
            skipped.append(item["student_name"])
            continue
        no = f"{prefix}-{today.year}-{item['enrollment_id']:04d}-{secrets.token_hex(2).upper()}"
        conn.execute("INSERT INTO certificates (enrollment_id, issued_on, certificate_no) VALUES (?,?,?)",
                     (item["enrollment_id"], today.isoformat(), no))
        issued.append({"name": item["student_name"], "certificate_no": no, "url": f"/api/certificates/{no}/view"})
    if not issued:
        raise ActionError("Nobody on this list qualifies any more, so no certificate was issued.", 409)
    msg = f"Issued {len(issued)} certificate(s)." + (f" Skipped (no longer eligible): {', '.join(skipped)}." if skipped else "")
    return {"issued": issued, "skipped": skipped, "message": msg,
            "links": [{"label": f"{x['name']} · {x['certificate_no']}", "url": x["url"]} for x in issued],
            "summary": {"issued": len(issued), "skipped": len(skipped)}}


def lookup(conn, number: str) -> dict | None:
    if not NUMBER.match(number or ""):
        return None
    row = conn.execute("""
        SELECT c.certificate_no, c.issued_on, s.name AS student_name, b.code AS batch_code, co.name AS course,
               co.duration_weeks, b.start_date, b.end_date, t.name AS trainer
        FROM certificates c JOIN enrollments e ON e.id = c.enrollment_id JOIN students s ON s.id = e.student_id
        JOIN batches b ON b.id = e.batch_id JOIN courses co ON co.id = b.course_id JOIN trainers t ON t.id = b.trainer_id
        WHERE c.certificate_no = ?""", (number,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["institute"] = db.institute_name(conn)
    out["valid"] = True
    return out


def _d(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d %B %Y")


def render(cert: dict, verify_url: str) -> str:
    e = {k: html.escape(str(v)) for k, v in cert.items()}
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Certificate {e['certificate_no']}</title>
<style>
  @page {{ size: A4 landscape; margin: 0; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f3ece2; font-family: Georgia, 'Times New Roman', serif; color: #3d2d22; }}
  .bar {{ display: flex; gap: 10px; justify-content: center; padding: 14px; font-family: system-ui, sans-serif; }}
  .bar button {{ padding: 9px 16px; border: 1px solid #e4593e; border-radius: 8px; background: #e4593e; color: #fff; font-size: 15px; cursor: pointer; }}
  .bar span {{ align-self: center; color: #1f7a45; font-size: 14px; }}
  .sheet {{ width: 297mm; height: 210mm; margin: 0 auto 24px; padding: 14mm; background: #fffdf8; }}
  .frame {{ height: 100%; padding: 14mm 18mm; border: 3px double #c9452c; display: flex; flex-direction: column; align-items: center; text-align: center; }}
  .inst {{ font-size: 15pt; letter-spacing: 4px; text-transform: uppercase; color: #8e3f2a; }}
  h1 {{ margin: 9mm 0 2mm; font-size: 38pt; font-weight: normal; color: #3d2d22; }}
  .sub {{ font-size: 13pt; color: #7a634d; letter-spacing: 1px; }}
  .name {{ margin: 9mm 0 3mm; font-size: 34pt; font-style: italic; color: #c9452c; border-bottom: 1px solid #e3d3bf; padding: 0 12mm 2mm; }}
  .text {{ max-width: 205mm; font-size: 14pt; line-height: 1.6; }}
  .foot {{ margin-top: auto; width: 100%; display: flex; justify-content: space-between; align-items: flex-end; font-size: 11pt; }}
  .sign {{ min-width: 60mm; border-top: 1px solid #a18a72; padding-top: 2mm; color: #5a4636; }}
  .no {{ font-family: 'Courier New', monospace; font-size: 10pt; color: #7a634d; line-height: 1.5; }}
  @media print {{ body {{ background: #fff; }} .bar {{ display: none; }} .sheet {{ margin: 0; }} }}
</style></head><body>
<div class="bar"><button onclick="window.print()">Print / Save as PDF</button><span>&#10003; Verified certificate issued by {e['institute']}</span></div>
<div class="sheet"><div class="frame">
  <div class="inst">{e['institute']}</div>
  <h1>Certificate of Completion</h1>
  <div class="sub">This is to certify that</div>
  <div class="name">{e['student_name']}</div>
  <div class="text">has successfully completed the course <b>{e['course']}</b> ({e['duration_weeks']} weeks, batch {e['batch_code']}),
  held from {html.escape(_d(cert['start_date']))} to {html.escape(_d(cert['end_date']))}, with the required attendance and all fees paid.</div>
  <div class="foot">
    <div class="sign">{e['trainer']}<br>Course trainer</div>
    <div class="no">Certificate no. {e['certificate_no']}<br>Issued {html.escape(_d(cert['issued_on']))}<br>Verify: {html.escape(verify_url)}</div>
    <div class="sign">Director<br>{e['institute']}</div>
  </div>
</div></div></body></html>"""
