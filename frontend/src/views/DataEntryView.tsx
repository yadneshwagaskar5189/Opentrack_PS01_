import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpenCheck, CalendarCheck, ChevronDown, Eye, IndianRupee, PenLine, Search, ShieldCheck, UserPlus } from 'lucide-react';
import type { Action, ActionType, AssignmentsInfo, EntryBatch, EntryOptions, OpenInstalment, Roster } from '../types';
import * as api from '../services/api';
import { ActionPreview } from '../components/ActionPreview';
import type { ViewProps } from './common';

const TABS: { id: ActionType; label: string; icon: typeof UserPlus; hint: string }[] = [
  { id: 'add_student', label: 'New student', icon: UserPlus, hint: 'Register a student and enrol them in a batch. Seat limits and phone numbers are checked.' },
  { id: 'record_payment', label: 'Record payment', icon: IndianRupee, hint: 'Record money received against an EMI. Duplicate UTRs and overpayments are blocked.' },
  { id: 'mark_attendance', label: 'Attendance', icon: CalendarCheck, hint: 'Mark one class. Trainers can only mark their own batches.' },
  { id: 'save_assignment', label: 'Assignment & marks', icon: BookOpenCheck, hint: 'Create an assignment and enter scores out of 100.' },
];

type Props = ViewProps;

// One page for typing data in. Every form makes a preview first; nothing is saved until "Confirm and save".
export function DataEntryView({ me, onToast, refreshPending }: Props) {
  const [opts, setOpts] = useState<EntryOptions | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<ActionType | null>(null);
  const load = useCallback(() => {
    api.entryOptions().then((o) => { setOpts(o); setTab((t) => (t && o.forms.includes(t) ? t : o.forms[0] ?? null)); })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const tabs = TABS.filter((t) => opts?.forms.includes(t.id));
  const current = TABS.find((t) => t.id === tab);
  const shared = { me, onToast, refresh: () => { load(); refreshPending(); }, opts: opts as EntryOptions };

  return <section className="view-page entry-view">
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><PenLine size={12} /></span> ADD DATA · PREVIEW FIRST, THEN CONFIRM</div>
      <h1>Add <span>data</span></h1>
      <p>Type new records in. Each form shows exactly what will be saved and checks it first; nothing changes until you confirm, and every save is in the audit log. You can also type these to the copilot.</p></div></div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {!opts && !error && <div className="loading-row"><span className="live-pip" /> Loading…</div>}
    {opts && tabs.length === 0 && <div className="empty-state panel"><b>Your role can't add data.</b></div>}
    {opts && tabs.length > 0 && <>
      <div className="approval-toolbar"><div className="filter-tabs" role="tablist" aria-label="Form">
        {tabs.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'selected' : ''} onClick={() => setTab(t.id)}><t.icon size={13} /> {t.label}</button>)}
      </div></div>
      {current && <p className="method-note"><ShieldCheck size={13} /> {current.hint}</p>}
      {tab === 'add_student' && <StudentForm {...shared} />}
      {tab === 'record_payment' && <PaymentForm {...shared} />}
      {tab === 'mark_attendance' && <AttendanceForm {...shared} />}
      {tab === 'save_assignment' && <AssignmentForm {...shared} />}
    </>}
  </section>;
}

type FormProps = { me: ViewProps['me']; onToast: ViewProps['onToast']; refresh: () => void; opts: EntryOptions };

// Shared preview / confirm column used by all four forms
function PreviewPane({ action, error, me, onToast, onDone }: { action: Action | null; error: string; me: ViewProps['me']; onToast: ViewProps['onToast']; onDone: (a: Action) => void }) {
  return <div className="entry-side">
    {error && <div className="inline-error entry-error" role="alert"><b>Not saved.</b> {error}</div>}
    {!action && !error && <div className="panel entry-empty"><Eye size={18} /><b>Preview appears here</b><p>Fill in the form and press Preview. You'll see every field and check before anything is saved.</p></div>}
    {action && <ActionPreview key={action.id} action={action} canDecide={me.allowed_actions.includes(action.action_type)} onToast={onToast} onDecided={onDone} />}
  </div>;
}

function usePreview(refresh: () => void) {
  const [action, setAction] = useState<Action | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function run(fn: () => Promise<Action>) {
    setBusy(true); setError(''); setAction(null);
    try { setAction(await fn()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return { action, error, busy, run, setAction, done: (a: Action) => { setAction(a); refresh(); } };
}

function BatchSelect({ batches, value, onChange, id, show }: { batches: EntryBatch[]; value: string; onChange: (v: string) => void; id: string; show: 'seats' | 'times' }) {
  return <div className="select-field"><select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
    {batches.map((b) => <option key={b.code} value={b.code}>{b.code} · {b.name}{show === 'seats'
      ? ` · online ${b.online_students}/${b.online_seat_limit} · class ${b.classroom_students}${b.room_capacity ? `/${b.room_capacity}` : ''}`
      : b.days ? ` · ${b.days.replaceAll(',', '/')} ${b.start_time ?? ''}` : ''}</option>)}
  </select><ChevronDown size={14} /></div>;
}

// ---------------------------------------------------------------- new student
function StudentForm({ me, onToast, refresh, opts }: FormProps) {
  const blank = { name: '', phone: '', email: '', city: '', parent_name: '', parent_phone: '', batch_code: opts.batches[0]?.code ?? '', learning_mode: 'classroom', instalments: '', discount: '' };
  const [f, setF] = useState(blank);
  const p = usePreview(refresh);
  const batch = opts.batches.find((b) => b.code === f.batch_code);
  const course = opts.courses?.find((c) => c.name === batch?.course_name);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const seatsLeft = batch ? batch.online_seat_limit - batch.online_students : 0;
  return <div className="entry-grid">
    <form className="panel entry-form" onSubmit={(e) => { e.preventDefault(); void p.run(() => api.proposeStudent({ ...f, instalments: f.instalments ? Number(f.instalments) : undefined, discount: Number(f.discount || 0) })); }}>
      <div className="form-grid two">
        <div><label className="field-label" htmlFor="st-name">STUDENT NAME *</label><input className="form-input" id="st-name" value={f.name} onChange={set('name')} placeholder="Riya Shah" required /></div>
        <div><label className="field-label" htmlFor="st-phone">PHONE (WHATSAPP) *</label><input className="form-input" id="st-phone" value={f.phone} onChange={set('phone')} placeholder="98765 43210" inputMode="tel" required /></div>
        <div><label className="field-label" htmlFor="st-parent">PARENT NAME</label><input className="form-input" id="st-parent" value={f.parent_name} onChange={set('parent_name')} placeholder="Sunil Shah" /></div>
        <div><label className="field-label" htmlFor="st-pphone">PARENT PHONE</label><input className="form-input" id="st-pphone" value={f.parent_phone} onChange={set('parent_phone')} inputMode="tel" /></div>
        <div><label className="field-label" htmlFor="st-email">EMAIL</label><input className="form-input" id="st-email" type="email" value={f.email} onChange={set('email')} /></div>
        <div><label className="field-label" htmlFor="st-city">CITY</label><input className="form-input" id="st-city" value={f.city} onChange={set('city')} placeholder="Lonavla" /></div>
      </div>
      <label className="field-label" htmlFor="st-batch">BATCH *</label>
      <BatchSelect id="st-batch" batches={opts.batches} value={f.batch_code} onChange={(v) => setF({ ...f, batch_code: v })} show="seats" />
      <div className="form-grid">
        <div><label className="field-label" htmlFor="st-mode">JOINS</label><div className="select-field"><select id="st-mode" value={f.learning_mode} onChange={set('learning_mode')}><option value="classroom">In class</option><option value="online">Online</option></select><ChevronDown size={14} /></div></div>
        <div><label className="field-label" htmlFor="st-emi">INSTALMENTS</label><input className="form-input" id="st-emi" type="number" min={1} max={12} value={f.instalments} onChange={set('instalments')} placeholder={String(course?.emi_count ?? 1)} /></div>
        <div><label className="field-label" htmlFor="st-disc">DISCOUNT ₹</label><input className="form-input" id="st-disc" type="number" min={0} value={f.discount} onChange={set('discount')} placeholder="0" /></div>
      </div>
      {batch && <p className="entry-hint">{course ? `${course.name}: ${api.rupees(course.total_fee)}, usually ${course.emi_count} EMI(s). ` : ''}
        {f.learning_mode === 'online' ? (seatsLeft > 0 ? `${seatsLeft} online seat(s) left in ${batch.code}.` : `${batch.code} has no online seats left: the preview will be refused.`) : `${batch.classroom_students} in class${batch.room_capacity ? ` of ${batch.room_capacity} seats` : ''}.`}</p>}
      <div className="entry-actions"><button type="button" className="text-button" onClick={() => { setF(blank); p.setAction(null); }}>Clear</button>
        <button className="simulate-button" disabled={p.busy}><Eye size={14} /> {p.busy ? 'Checking…' : 'Preview'}</button></div>
    </form>
    <PreviewPane {...p} me={me} onToast={onToast} onDone={(a) => { p.done(a); if (a.status === 'approved') setF(blank); }} />
  </div>;
}

// ---------------------------------------------------------------- payment
function PaymentForm({ me, onToast, refresh }: FormProps) {
  const [q, setQ] = useState('');
  const [list, setList] = useState<OpenInstalment[] | null>(null);
  const [pick, setPick] = useState<OpenInstalment | null>(null);
  const [f, setF] = useState({ amount: '', method: 'UPI', reference: '', paid_on: '' });
  const p = usePreview(refresh);
  const search = useCallback((text: string) => { api.entryInstalments(text).then(setList).catch((e: Error) => onToast(e.message, 'error')); }, [onToast]);
  useEffect(() => { const t = window.setTimeout(() => search(q), 250); return () => window.clearTimeout(t); }, [q, search]);
  const choose = (i: OpenInstalment) => { setPick(i); setF({ ...f, amount: String(i.balance) }); p.setAction(null); };
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return <div className="entry-grid">
    <form className="panel entry-form" onSubmit={(e) => { e.preventDefault(); if (pick) void p.run(() => api.proposePayment({ instalment_id: pick.instalment_id, ...f, amount: Number(f.amount) })); }}>
      <label className="field-label" htmlFor="pay-q">FIND THE STUDENT OR BATCH</label>
      <label className="audit-search"><Search size={14} /><input id="pay-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Rohan or B02" /></label>
      <div className="data-table-wrap entry-pick"><table className="data-table compact-table"><thead><tr><th /><th>Student</th><th>EMI</th><th>Due</th><th>Balance</th><th>Status</th></tr></thead>
        <tbody>{(list ?? []).slice(0, 12).map((i) => <tr key={i.instalment_id} className={pick?.instalment_id === i.instalment_id ? 'picked' : ''} onClick={() => choose(i)}>
          <td><input type="radio" name="inst" checked={pick?.instalment_id === i.instalment_id} onChange={() => choose(i)} aria-label={`${i.student_name} EMI ${i.instalment_no}`} /></td>
          <td>{i.student_name} <span className="mono-cell">{i.batch_code}</span></td><td>{i.instalment_no}</td><td className="mono-cell">{api.fmtDate(i.due_date)}</td>
          <td>{api.rupees(i.balance)}</td><td><span className={`status-pill ${i.status === 'overdue' ? 'status-bad' : i.status === 'partial' ? 'status-warning' : 'status-review'}`}><i />{i.status}</span></td></tr>)}</tbody></table>
        {list && list.length === 0 && <p className="dash-empty">No unpaid EMIs match.</p>}</div>
      <div className="form-grid two">
        <div><label className="field-label" htmlFor="pay-amt">AMOUNT RECEIVED ₹ *</label><input className="form-input" id="pay-amt" type="number" min={1} value={f.amount} onChange={set('amount')} required /></div>
        <div><label className="field-label" htmlFor="pay-method">METHOD</label><div className="select-field"><select id="pay-method" value={f.method} onChange={set('method')}><option value="UPI">UPI</option><option value="bank_transfer">Bank transfer</option><option value="cash">Cash</option><option value="card">Card</option></select><ChevronDown size={14} /></div></div>
        <div><label className="field-label" htmlFor="pay-ref">{f.method === 'cash' ? 'RECEIPT NO. (OPTIONAL)' : 'UPI REF / UTR *'}</label><input className="form-input" id="pay-ref" value={f.reference} onChange={set('reference')} placeholder={f.method === 'cash' ? 'RCPT-1234' : '412398765012'} /></div>
        <div><label className="field-label" htmlFor="pay-date">PAID ON</label><input className="form-input" id="pay-date" type="date" value={f.paid_on} onChange={set('paid_on')} /></div>
      </div>
      <div className="entry-actions"><span className="entry-hint">{pick ? `${pick.student_name}, EMI ${pick.instalment_no}: ${api.rupees(pick.balance)} due` : 'Pick an EMI above'}</span>
        <button className="simulate-button" disabled={p.busy || !pick}><Eye size={14} /> {p.busy ? 'Checking…' : 'Preview'}</button></div>
    </form>
    <PreviewPane {...p} me={me} onToast={onToast} onDone={(a) => { p.done(a); if (a.status === 'approved') { setPick(null); setF({ amount: '', method: 'UPI', reference: '', paid_on: '' }); search(q); } }} />
  </div>;
}

// ---------------------------------------------------------------- attendance
const STATES = ['present', 'late', 'absent'] as const;
function AttendanceForm({ me, onToast, refresh, opts }: FormProps) {
  const [batch, setBatch] = useState(opts.batches[0]?.code ?? '');
  const [day, setDay] = useState(opts.today);
  const [topic, setTopic] = useState('');
  const [roster, setRoster] = useState<Roster | null>(null);
  const [marks, setMarks] = useState<Record<number, string>>({});
  const p = usePreview(refresh);
  const load = useCallback(() => {
    if (!batch) return;
    api.entryRoster(batch, day).then((r) => {
      setRoster(r); setTopic(r.topic ?? '');
      setMarks(Object.fromEntries(r.students.map((s) => [s.student_id, s.status ?? 'present'])));
    }).catch((e: Error) => onToast(e.message, 'error'));
  }, [batch, day, onToast]);
  useEffect(load, [load]);
  const counts = useMemo(() => STATES.map((s) => Object.values(marks).filter((m) => m === s).length), [marks]);
  if (opts.batches.length === 0) return <div className="empty-state panel"><b>No running batches to mark.</b></div>;
  return <div className="entry-grid">
    <form className="panel entry-form" onSubmit={(e) => { e.preventDefault(); void p.run(() => api.proposeAttendance({ batch_code: batch, date: day, topic, records: Object.entries(marks).map(([id, status]) => ({ student_id: Number(id), status })) })); }}>
      <div className="form-grid">
        <div style={{ gridColumn: 'span 2' }}><label className="field-label" htmlFor="att-batch">BATCH</label><BatchSelect id="att-batch" batches={opts.batches} value={batch} onChange={setBatch} show="times" /></div>
        <div><label className="field-label" htmlFor="att-date">DATE</label><input className="form-input" id="att-date" type="date" max={opts.today} value={day} onChange={(e) => setDay(e.target.value)} /></div>
      </div>
      <label className="field-label" htmlFor="att-topic">TOPIC (OPTIONAL)</label>
      <input className="form-input" id="att-topic" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Spring Boot REST" />
      {roster?.usual_day === false && <p className="entry-hint warn">{roster.batch.code} doesn't usually meet on this day; it will be saved as an extra class.</p>}
      {roster?.session_id && <p className="entry-hint">Attendance already exists for this class; saving replaces it.</p>}
      <div className="att-bar"><span>{counts[0]} present · {counts[1]} late · {counts[2]} absent</span>
        <button type="button" className="text-button" onClick={() => setMarks(Object.fromEntries(Object.keys(marks).map((k) => [k, 'present'])))}>All present</button></div>
      <ul className="att-list">{roster?.students.map((s) => <li key={s.student_id}>
        <span><b>{s.name}</b><small>{s.learning_mode === 'online' ? 'Online' : 'In class'}</small></span>
        <div className="att-toggle" role="radiogroup" aria-label={`Attendance for ${s.name}`}>{STATES.map((st) =>
          <button type="button" key={st} role="radio" aria-checked={marks[s.student_id] === st} className={`att-${st} ${marks[s.student_id] === st ? 'on' : ''}`}
                  onClick={() => setMarks({ ...marks, [s.student_id]: st })}>{st[0].toUpperCase() + st.slice(1)}</button>)}</div>
      </li>)}</ul>
      <div className="entry-actions"><span className="entry-hint">{roster?.students.length ?? 0} students</span>
        <button className="simulate-button" disabled={p.busy || !roster?.students.length}><Eye size={14} /> {p.busy ? 'Checking…' : 'Preview'}</button></div>
    </form>
    <PreviewPane {...p} me={me} onToast={onToast} onDone={(a) => { p.done(a); if (a.status === 'approved') load(); }} />
  </div>;
}

// ---------------------------------------------------------------- assignment and marks
function AssignmentForm({ me, onToast, refresh, opts }: FormProps) {
  const [batch, setBatch] = useState(opts.batches[0]?.code ?? '');
  const [info, setInfo] = useState<AssignmentsInfo | null>(null);
  const [which, setWhich] = useState('new');
  const [title, setTitle] = useState('');
  const [due, setDue] = useState('');
  const [scores, setScores] = useState<Record<number, string>>({});
  const p = usePreview(refresh);
  const load = useCallback(() => {
    if (!batch) return;
    api.entryAssignments(batch).then((d) => { setInfo(d); }).catch((e: Error) => onToast(e.message, 'error'));
  }, [batch, onToast]);
  useEffect(load, [load]);
  useEffect(() => {                                   // show existing marks when an assignment is picked
    const existing = info?.scores[which] ?? {};
    setScores(Object.fromEntries(Object.entries(existing).map(([sid, v]) => [Number(sid), v.score == null ? '' : String(v.score)])));
  }, [which, info]);
  if (opts.batches.length === 0) return <div className="empty-state panel"><b>No running batches.</b></div>;
  const spec = () => ({ batch_code: batch, ...(which === 'new' ? { title, due_date: due } : { assignment_id: Number(which) }),
    scores: Object.entries(scores).filter(([, v]) => v !== '').map(([sid, v]) => ({ student_id: Number(sid), score: Number(v) })) });
  return <div className="entry-grid">
    <form className="panel entry-form" onSubmit={(e) => { e.preventDefault(); void p.run(() => api.proposeAssignment(spec())); }}>
      <div className="form-grid two">
        <div><label className="field-label" htmlFor="as-batch">BATCH</label><BatchSelect id="as-batch" batches={opts.batches} value={batch} onChange={(v) => { setBatch(v); setWhich('new'); }} show="times" /></div>
        <div><label className="field-label" htmlFor="as-which">ASSIGNMENT</label><div className="select-field"><select id="as-which" value={which} onChange={(e) => setWhich(e.target.value)}>
          <option value="new">+ New assignment</option>
          {info?.assignments.map((a) => <option key={a.id} value={a.id}>{a.title} · due {a.due_date} · {a.submitted} marked</option>)}
        </select><ChevronDown size={14} /></div></div>
      </div>
      {which === 'new' && <div className="form-grid two">
        <div><label className="field-label" htmlFor="as-title">TITLE *</label><input className="form-input" id="as-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Mini project: REST API" /></div>
        <div><label className="field-label" htmlFor="as-due">DUE DATE *</label><input className="form-input" id="as-due" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></div>
      </div>}
      <span className="field-label">SCORES OUT OF 100 (LEAVE BLANK IF NOT SUBMITTED)</span>
      <ul className="att-list score-list">{info?.students.map((s) => <li key={s.student_id}><span><b>{s.name}</b><small>{s.learning_mode === 'online' ? 'Online' : 'In class'}</small></span>
        <input className="form-input score-input" type="number" min={0} max={100} value={scores[s.student_id] ?? ''} onChange={(e) => setScores({ ...scores, [s.student_id]: e.target.value })} aria-label={`Score for ${s.name}`} /></li>)}</ul>
      <div className="entry-actions"><span className="entry-hint">{Object.values(scores).filter((v) => v !== '').length} score(s) entered</span>
        <button className="simulate-button" disabled={p.busy || (which === 'new' && (!title.trim() || !due))}><Eye size={14} /> {p.busy ? 'Checking…' : 'Preview'}</button></div>
    </form>
    <PreviewPane {...p} me={me} onToast={onToast} onDone={(a) => { p.done(a); if (a.status === 'approved') { setTitle(''); setDue(''); setWhich('new'); load(); } }} />
  </div>;
}
