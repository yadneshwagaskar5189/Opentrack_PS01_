"""Fee reconciliation: match bank statement credits against recorded payments.

Each bank line gets one status, with a plain-language note:
  matched      UTR equals a payment reference and the amount agrees
  partial      UTR matches but the bank received less (or more) than was recorded
  duplicate    the same UTR appears twice on the statement
  unmatched    no payment has this UTR (we suggest a likely student when the payer is a parent)
  not_fee      bank interest or another non-student credit
and every UPI / bank-transfer payment in the statement period with no bank line is
reported as not_in_bank. Read-only: nothing is changed here.
"""
import re
from difflib import SequenceMatcher

TITLES = re.compile(r"^(mr|mrs|ms|miss|dr|shri|smt)\.?\s+", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z ]", "", TITLES.sub("", (name or "").strip()).lower()).strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _payer(description: str) -> str:
    parts = [p.strip() for p in (description or "").split("/") if p.strip()]
    return parts[-1] if len(parts) >= 3 else (description or "").strip()


def _rupees(n) -> str:
    return f"₹{int(n or 0):,}"


def reconcile(conn) -> dict:
    bank = [dict(r) for r in conn.execute(
        "SELECT id, txn_ref, txn_date, description, credit, utr FROM bank_transactions ORDER BY txn_date, id")]
    payments = [dict(r) for r in conn.execute("""
        SELECT p.id, p.amount, p.paid_on, p.method, p.reference, p.student_id, s.name AS student_name,
               b.code AS batch_code, i.instalment_no
        FROM payments p JOIN students s ON s.id = p.student_id
        LEFT JOIN instalments i ON i.id = p.instalment_id
        LEFT JOIN fee_plans fp ON fp.id = i.fee_plan_id
        LEFT JOIN enrollments e ON e.id = fp.enrollment_id
        LEFT JOIN batches b ON b.id = e.batch_id""")]
    students = [dict(r) for r in conn.execute("SELECT id, name, parent_name FROM students")]
    open_emis = {}
    for r in conn.execute("""
        SELECT e.student_id, b.code AS batch_code, i.instalment_no, i.due_date, i.amount - i.paid_amount AS balance, i.status
        FROM instalments i JOIN fee_plans fp ON fp.id = i.fee_plan_id JOIN enrollments e ON e.id = fp.enrollment_id
        JOIN batches b ON b.id = e.batch_id WHERE i.status != 'paid' ORDER BY i.due_date"""):
        open_emis.setdefault(r["student_id"], []).append(dict(r))

    by_ref: dict[str, dict] = {}
    for p in payments:
        if p["reference"]:
            by_ref.setdefault(p["reference"], p)

    lines, matched_payments, first_seen = [], set(), {}
    for t in bank:
        payer = _payer(t["description"])
        line = {**t, "payer": payer, "status": "", "note": "", "payment_id": None, "student_name": None,
                "recorded_amount": None}
        utr = t["utr"]
        if utr and utr in first_seen:
            first = first_seen[utr]
            line.update(status="duplicate",
                        note=f"Same UTR as {first['txn_ref'] or first['id']} on {first['txn_date']}. The bank credited "
                             f"{_rupees(t['credit'])} twice for one payment: confirm with the bank before using it.")
            lines.append(line)
            continue
        if utr:
            first_seen[utr] = t
        p = by_ref.get(utr) if utr else None
        if p:
            matched_payments.add(p["id"])
            line.update(payment_id=p["id"], student_name=p["student_name"], recorded_amount=p["amount"])
            if t["credit"] == p["amount"]:
                line["status"] = "matched"
                if _norm(payer) != _norm(p["student_name"]):
                    line["note"] = (f"Matched on UTR. The bank line spells the payer as '{payer}', not "
                                    f"{p['student_name']}, so it was matched by reference, not by name.")
                else:
                    line["note"] = f"{p['student_name']}, {p['batch_code'] or ''} EMI {p['instalment_no'] or ''}".strip()
            else:
                diff = p["amount"] - t["credit"]
                line.update(status="partial",
                            note=(f"Bank received {_rupees(t['credit'])} but {_rupees(p['amount'])} is recorded for "
                                  f"{p['student_name']}: short by {_rupees(diff)}." if diff > 0 else
                                  f"Bank received {_rupees(t['credit'])}, more than the {_rupees(p['amount'])} recorded "
                                  f"for {p['student_name']}."))
            lines.append(line)
            continue
        if not utr or re.search(r"interest|int\.pd|charges|reversal", t["description"], re.I):
            line.update(status="not_fee", note="Bank interest or a bank entry, not a student payment.")
            lines.append(line)
            continue
        # unmatched: is the payer a student or a parent we know?
        best, kind = None, ""
        for s in students:
            for who, label in ((s["name"], "student"), (s["parent_name"], "parent")):
                if who and _similar(payer, who) >= 0.85 and (best is None or _similar(payer, who) > best[0]):
                    best, kind = (_similar(payer, who), s), label
        if best is None:
            # same surname as exactly one student: probably a family member paying
            surname = _norm(payer).split(" ")[-1] if _norm(payer) else ""
            family = [s for s in students if surname and _norm(s["name"]).split(" ")[-1] == surname]
            if len(family) == 1:
                best, kind = (0.0, family[0]), "family"
        line["status"] = "unmatched"
        if best:
            s = best[1]
            emis = open_emis.get(s["id"], [])
            due = ", ".join(f"EMI {e['instalment_no']} {_rupees(e['balance'])} ({e['status']}, due {e['due_date']})"
                            for e in emis[:3]) or "no open EMIs"
            fits = any(e["balance"] == t["credit"] for e in emis)
            who = {"student": f"the payer looks like {s['name']}",
                   "parent": f"the payer looks like {s['parent_name']}, parent of {s['name']}",
                   "family": f"the payer shares a surname with {s['name']} (parent on file: {s['parent_name'] or 'none'}), "
                             f"so it may be a family member paying for them"}[kind]
            line.update(student_name=s["name"],
                        note=(f"No payment has this UTR; {who}. Open: {due}."
                              + ("" if fits else f" {_rupees(t['credit'])} doesn't equal any open EMI, so confirm with the family before recording it.")))
        else:
            line["note"] = "No payment has this UTR and the payer doesn't match any student or parent. Ask the bank for details."
        lines.append(line)

    period = (bank[0]["txn_date"], bank[-1]["txn_date"]) if bank else (None, None)
    not_in_bank = []
    if bank:
        for p in payments:
            if (p["method"] in ("UPI", "bank_transfer") and p["id"] not in matched_payments
                    and period[0] <= p["paid_on"] <= period[1] and not (p["reference"] or "").startswith("APPROVED-ACTION")):
                not_in_bank.append({**p, "status": "not_in_bank",
                                    "note": f"Recorded as paid by {p['method'].replace('_', ' ')} on {p['paid_on']} "
                                            f"(ref {p['reference']}) but no bank credit has this reference."})

    counts: dict[str, int] = {}
    for x in lines + not_in_bank:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    needs_action = [x for x in lines if x["status"] != "matched" or x["note"].startswith("Matched on UTR")]
    return {
        "period": {"from": period[0], "to": period[1]},
        "summary": counts,
        "bank_total": sum(t["credit"] for t in bank),
        "lines": lines,
        "not_in_bank": not_in_bank,
        "needs_attention": len(needs_action) + len(not_in_bank),
        "method": "A bank line matches a payment when its UTR equals the payment reference. Amounts are then compared; "
                  "repeated UTRs are duplicates; unknown payers are compared with student and parent names.",
    }
