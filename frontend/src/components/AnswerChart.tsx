import { useMemo, useState } from 'react';
import { ChartBar, ChevronDown, ChevronRight } from 'lucide-react';
import type { Row } from '../types';
import { formatCell, prettyCol } from '../services/api';

// A small bar chart above the results table when an answer is "a number per thing"
// (pending fees per batch, students per batch, attendance % per student...). One series, one colour:
// the title names the measure, values sit at the bar tips in text colour, hover shows the full row.

const LABEL_PREF = ['batch_code', 'code', 'course', 'course_name', 'trainer_name', 'student_name', 'name', 'method', 'room', 'room_name', 'day_name'];
const VALUE_PREF = ['pending', 'overdue', 'collected', 'amount_due', 'balance', 'total_fee', 'per_emi', 'attendance_pct', 'total',
  'students', 'lectures', 'payments', 'classes_missed', 'absent', 'present', 'online_seats_left', 'enrolled', 'credit', 'amount', 'count'];
const NOT_A_MEASURE = /(^id$|_id$|_no$|^instalment|phone|year|hour|_time$|^capacity$|has_projector|eligible|on_leave|duration_weeks|emis$)/i;
const DATE = /^\d{4}-\d{2}-\d{2}/;

type Plan = { label: string; value: string; max: number; items: { label: string; value: number; row: Row }[] };

export function planChart(columns: string[], rows: Row[]): Plan | null {
  if (rows.length < 2 || rows.length > 25) return null;
  const numeric = columns.filter((c) => rows.every((r) => typeof r[c] === 'number' || r[c] === null) && rows.some((r) => typeof r[c] === 'number'));
  const text = columns.filter((c) => !numeric.includes(c) && rows.every((r) => typeof r[c] === 'string' && !DATE.test(String(r[c]))));
  const unique = (c: string) => new Set(rows.map((r) => String(r[c]))).size === rows.length;   // one bar per thing
  const label = [...LABEL_PREF.filter((c) => text.includes(c)), ...text].find(unique);
  const candidates = numeric.filter((c) => !NOT_A_MEASURE.test(c));
  const value = VALUE_PREF.find((c) => candidates.includes(c)) ?? candidates[0];
  if (!label || !value) return null;
  const items = rows.map((r) => ({ label: String(r[label]), value: Number(r[value] ?? 0), row: r }));
  if (items.every((i) => i.value === items[0].value)) return null;            // nothing to compare
  if (items.some((i) => i.value < 0)) return null;                            // bars grow from zero only
  const max = /pct|percent/i.test(value) ? 100 : Math.max(...items.map((i) => i.value));
  return max > 0 ? { label, value, max, items } : null;
}

export function AnswerChart({ columns, rows }: { columns: string[]; rows: Row[] }) {
  const plan = useMemo(() => planChart(columns, rows), [columns, rows]);
  const [open, setOpen] = useState(true);
  const [hover, setHover] = useState<number | null>(null);
  if (!plan) return null;
  const fmt = (v: number) => (/pct|percent/i.test(plan.value) ? `${v}%` : formatCell(plan.value, v));
  const others = columns.filter((c) => c !== plan.label && c !== plan.value).slice(0, 4);
  return (
    <div className="answer-chart">
      <button className="answer-chart-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChartBar size={14} /><b>{prettyCol(plan.value)}</b><span>by {prettyCol(plan.label).toLowerCase()}</span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && <div className="answer-bars" role="img" aria-label={`${prettyCol(plan.value)} by ${prettyCol(plan.label)}`}>
        {plan.items.map((it, i) => (
          <div key={it.label} className={`answer-bar-row ${hover === i ? 'hovered' : ''}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <span className="answer-bar-label" title={it.label}>{it.label}</span>
            <span className="answer-bar-track">
              <span className="answer-bar" style={{ width: `calc((100% - 104px) * ${Math.max(it.value / plan.max, it.value > 0 ? 0.012 : 0)})` }} />
              <span className="answer-bar-value">{fmt(it.value)}</span>
              {hover === i && others.length > 0 && <span className="answer-bar-tip">
                <b>{it.label}</b>{others.map((c) => <span key={c}>{prettyCol(c)}: {formatCell(c, it.row[c] as string | number | null)}</span>)}
              </span>}
            </span>
          </div>
        ))}
      </div>}
    </div>
  );
}
