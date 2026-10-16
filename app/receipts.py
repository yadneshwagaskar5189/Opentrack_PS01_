"""Fee receipts: every payment saved through an approval gets a receipt number and a printable page.

The page link carries a signature (HMAC with the secret kept outside the database), so receipts
can't be found by guessing numbers. A "Send receipt on WhatsApp" link follows the same
who-may-receive rule as every other message (see whatsapp.py).
"""
import hashlib
import hmac
import html
from datetime import date

from . import audit, db, whatsapp

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
        "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    return ONES[n] if n < 20 else (TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else ""))


def in_words(n: int) -> str:
    """Indian numbering: 1,23,456 -> One Lakh Twenty Three Thousand Four Hundred Fifty Six."""
    n = int(n)
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand"), (100, "Hundred")):
        if n >= size:
            parts.append(f"{in_words(n // size) if size == 10_000_000 else _two(n // size)} {name}")
            n %= size
    if n:
        parts.append(_two(n))
    return " ".join(parts)


def rupees(n) -> str:
    s = str(int(n or 0))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        s = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1]) + "," + tail
    return f"₹{s}"


def sign(number: str) -> str:
    return hmac.new(audit._key(), f"receipt:{number}".encode(), hashlib.sha256).hexdigest()[:20]


def _info(conn, where: str, arg) -> dict | None:
    row = conn.execute(f"""
        SELECT p.id, p.receipt_no, p.amount, p.paid_on, p.method, p.reference,
               s.name AS student_name, s.phone, s.real_contact, b.code AS batch_code, c.name AS course,
               i.instalment_no, i.amount AS emi_amount, i.paid_amount AS emi_paid, i.status AS emi_status, fp.id AS fee_plan_id
        FROM payments p JOIN students s ON s.id = p.student_id
        LEFT JOIN instalments i ON i.id = p.instalment_id LEFT JOIN fee_plans fp ON fp.id = i.fee_plan_id
        LEFT JOIN enrollments e ON e.id = fp.enrollment_id LEFT JOIN batches b ON b.id = e.batch_id
        LEFT JOIN courses c ON c.id = b.course_id WHERE {where}""", (arg,)).fetchone()
    if not row:
        return None
    r = dict(row)
    r["outstanding"] = conn.execute("SELECT COALESCE(SUM(amount - paid_amount), 0) FROM instalments WHERE fee_plan_id=?",
                                    (r["fee_plan_id"],)).fetchone()[0] if r["fee_plan_id"] else None
    r["institute"] = db.institute_name(conn)
    return r


def issue(conn, payment_id: int) -> dict:
    """Give a saved payment its receipt number; returns links for the approval result."""
    r = _info(conn, "p.id = ?", payment_id)
    number = r["receipt_no"] or f"RCPT-{r['paid_on'][:4]}-{payment_id:05d}"
    conn.execute("UPDATE payments SET receipt_no=? WHERE id=?", (number, payment_id))
    url = f"/api/receipts/{number}/view?sig={sign(number)}"
    when = date.fromisoformat(r["paid_on"]).strftime("%d %b %Y")
    text = (f"Namaste {r['student_name']}, {r['institute']} has received {rupees(r['amount'])} "
            f"({in_words(r['amount'])} rupees) on {when} by {r['method'].replace('_', ' ')}"
            + (f" for {r['course']} ({r['batch_code']}), EMI {r['instalment_no']}" if r["course"] else "")
            + f". Receipt no. {number}."
            + (f" Fees still outstanding: {rupees(r['outstanding'])}." if r["outstanding"] else " Your fees are fully paid.")
            + " Thank you!")
    links = [{"label": f"Receipt {number}", "url": url}]
    wa = whatsapp.link(r["phone"], text, r["student_name"], bool(r["real_contact"]))
    if wa:
        links.append({"label": f"Send receipt on WhatsApp ({wa['to']})", "url": wa["url"], "kind": "whatsapp"})
    return {"receipt_no": number, "url": url, "text": text, "links": links}


def lookup(conn, number: str, sig: str) -> dict | None:
    if not number or not sig or not hmac.compare_digest(sign(number), sig):
        return None
    return _info(conn, "p.receipt_no = ?", number)


def render(r: dict) -> str:
    e = {k: html.escape(str(v)) for k, v in r.items() if v is not None}
    when = date.fromisoformat(r["paid_on"]).strftime("%d %B %Y")
    rows = [("Received from", e["student_name"]),
            ("Course", f"{e['course']} · batch {e['batch_code']}" if r["course"] else "–"),
            ("For", f"EMI {e['instalment_no']}" if r["instalment_no"] else "Fees"),
            ("Amount", f"<b>{html.escape(rupees(r['amount']))}</b> ({html.escape(in_words(r['amount']))} rupees only)"),
            ("Paid on", html.escape(when)),
            ("Method", html.escape(r["method"].replace("_", " "))),
            ("Reference", e.get("reference", "–")),
            ("EMI status now", e.get("emi_status", "–")),
            ("Fees still outstanding", html.escape(rupees(r["outstanding"])) if r["outstanding"] is not None else "–")]
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Receipt {e['receipt_no']}</title>
<style>
  @page {{ size: A5; margin: 12mm; }}
  body {{ margin: 0; background: #f3ece2; font-family: system-ui, 'Segoe UI', Arial, sans-serif; color: #3d2d22; }}
  .bar {{ display: flex; justify-content: center; padding: 14px; }}
  .bar button {{ padding: 9px 16px; border: 1px solid #e4593e; border-radius: 8px; background: #e4593e; color: #fff; font-size: 15px; cursor: pointer; }}
  .sheet {{ max-width: 640px; margin: 0 auto 24px; padding: 28px 32px; background: #fffdf8; border: 1px solid #ead8c1; border-radius: 10px; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; border-bottom: 2px solid #e4593e; padding-bottom: 12px; }}
  .head h1 {{ margin: 0; font-size: 22px; }}
  .head small {{ color: #8a735d; }}
  .no {{ text-align: right; font-size: 14px; color: #5a4636; }}
  .no b {{ display: block; font-size: 16px; color: #3d2d22; }}
  h2 {{ margin: 18px 0 8px; font-size: 13px; letter-spacing: 2px; color: #b4532f; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
  th {{ width: 42%; padding: 8px 0; text-align: left; font-weight: 500; color: #7a634d; vertical-align: top; }}
  td {{ padding: 8px 0; border-bottom: 1px solid #f0e4d4; }}
  .foot {{ margin-top: 18px; font-size: 12.5px; color: #8a735d; }}
  @media print {{ body {{ background: #fff; }} .bar {{ display: none; }} .sheet {{ border: 0; margin: 0; }} }}
</style></head><body>
<div class="bar"><button onclick="window.print()">Print / Save as PDF</button></div>
<div class="sheet">
  <div class="head"><div><h1>{e['institute']}</h1><small>Fee receipt</small></div>
    <div class="no">Receipt no.<b>{e['receipt_no']}</b>{html.escape(when)}</div></div>
  <h2>PAYMENT RECEIVED</h2>
  <table>{body}</table>
  <div class="foot">This is a computer-generated receipt and needs no signature. It was issued after a staff member
  approved the payment, and the approval is recorded in the institute's audit log.</div>
</div></body></html>"""
