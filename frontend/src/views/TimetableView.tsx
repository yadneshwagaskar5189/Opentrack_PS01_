import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { ArrowLeftRight, ArrowRight, CalendarDays, Check, ChevronDown, CircleHelp, Plus, ShieldCheck, Sparkles, UserRoundX, Users, WandSparkles, X, Zap } from 'lucide-react';
import type { BatchRequest, Course, Health, PlannerResult, Slot, Timetable, Trainer } from '../types';
import * as api from '../services/api';
import { Modal } from '../components/Feedback';
import type { ViewProps } from './common';

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// One colour per course, in a stable order (the legend shows which is which)
const PALETTE = ['violet', 'blue', 'pink', 'amber', 'teal', 'plum'];
const courseOf = (s: Slot) => s.course_name || s.batch_code;
const MODE: Record<string, string> = { classroom: 'Classroom', online: 'Online', hybrid: 'Hybrid' };

export function TimetableView({ me, onToast, onNavigate, refreshPending, health }: ViewProps & { health: Health | null }) {
  const isOwner = me.user.role === 'owner';
  const [tt, setTt] = useState<Timetable | null>(null);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState<PlannerResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<null | 'unavailable' | 'batch'>(null);
  const [trainers, setTrainers] = useState<Trainer[]>([]);
  const [pick, setPick] = useState<{ trainerId: number | null; days: string[]; reason: string }>({ trainerId: null, days: [], reason: 'sick leave' });

  const load = useCallback(() => {
    setError('');
    api.getTimetable().then(setTt).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  useEffect(() => { if (isOwner) api.getTrainers().then(setTrainers).catch(() => undefined); }, [isOwner]);

  const starts = useMemo(() => [...new Set((tt?.slots ?? []).map((s) => s.start_hour))].sort((a, b) => a - b), [tt]);
  const trainersCount = useMemo(() => new Set((tt?.slots ?? []).map((s) => s.trainer_id)).size, [tt]);
  const courses = useMemo(() => [...new Set((tt?.slots ?? []).map(courseOf))].sort(), [tt]);
  const tone = (s: Slot) => PALETTE[Math.max(0, courses.indexOf(courseOf(s))) % PALETTE.length];
  const weekStart = tt ? new Date(`${tt.week_start}T00:00:00`) : null;
  const todayIndex = (() => {
    const t = health?.today ? new Date(`${health.today}T00:00:00`) : new Date();
    if (!weekStart) return -1;
    const diff = Math.round((t.getTime() - weekStart.getTime()) / 86400000);
    return diff >= 0 && diff < 7 ? diff : -1;      // only when today is inside the week shown
  })();
  const nextWeek = todayIndex === -1 && weekStart !== null && weekStart.getTime() > Date.parse(`${health?.today ?? ''}T00:00:00`);
  const dateOf = (i: number) => (weekStart ? new Date(weekStart.getTime() + i * 86400000).getDate() : '');
  const changed = new Set((plan?.proposal.changes ?? []).map((c) => c.batch_code));

  async function run(promise: Promise<PlannerResult>) {
    setBusy(true);
    try {
      const res = await promise;
      setPlan(res);
      setDialog(null);
      onToast(res.action ? `${res.proposal.changes.length} change(s) proposed in ${res.proposal.elapsed_ms} ms` : res.message, res.action ? 'warning' : 'info');
      refreshPending();
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function approvePlan() {
    if (!plan?.action) return;
    setBusy(true);
    try {
      await api.approveAction(plan.action.id);
      onToast('Timetable updated · decision recorded in the audit log', 'success');
      setPlan(null);
      load();
      refreshPending();
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }
  async function discardPlan() {
    if (!plan?.action) { setPlan(null); return; }
    setBusy(true);
    try {
      await api.rejectAction(plan.action.id, 'Discarded from the timetable page');
      setPlan(null);
      refreshPending();
      onToast('Proposal discarded. The timetable is unchanged.', 'info');
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  function openUnavailable(slot?: Slot) {
    if (!isOwner) { onToast('Only the owner can change the timetable', 'info'); return; }
    setPick({ trainerId: slot?.trainer_id ?? trainers[0]?.id ?? null, days: slot ? [SHORT[slot.day_of_week]] : [], reason: 'sick leave' });
    setDialog('unavailable');
  }

  if (!tt) return <section className="view-page timetable-view">{error ? <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div> : <div className="loading-row"><span className="live-pip" /> Loading this week's timetable…</div>}</section>;

  return <section className="view-page timetable-view">
    <div className="page-heading timetable-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><CalendarDays size={12} /></span> {nextWeek ? 'PLANNING NEXT WEEK' : 'CAPACITY, AT A GLANCE'}</div><h1>Weekly <span>timetable</span></h1><p>Clash-free by construction. When a trainer is away or a batch is added, it re-plans in milliseconds and explains every move.</p></div>
      <div className="timetable-head-actions">
        <div className="week-picker"><span><CalendarDays size={13} /> {api.fmtDay(tt.week_start)} – {api.fmtDay(tt.week_end)}</span></div>
        {isOwner && <>
          <button className="outline-action" onClick={() => void run(api.fixClashes())} disabled={busy}><WandSparkles size={14} /> Fix clashes</button>
          <button className="outline-action" onClick={() => setDialog('batch')} disabled={busy}><Plus size={14} /> Add a batch</button>
          <button className="unavailable-action" onClick={() => openUnavailable()} disabled={busy}><UserRoundX size={14} /> Mark trainer unavailable</button>
        </>}
      </div>
    </div>
    <div className="schedule-stats">
      <div className="schedule-stat"><span className="schedule-stat-icon stat-violet"><CalendarDays size={15} /></span><div><b>{String(tt.slots.length).padStart(2, '0')}</b><span>Classes this week</span></div><small>{tt.online_licences} online licences</small></div>
      <div className="schedule-stat"><span className="schedule-stat-icon stat-green"><Users size={15} /></span><div><b>{String(trainersCount).padStart(2, '0')}</b><span>Trainers teaching</span></div><small>{me.user.role === 'trainer' ? 'Your classes only' : 'Across all batches'}</small></div>
      <div className="schedule-stat"><span className="schedule-stat-icon stat-orange"><Zap size={15} /></span><div><b>{String(tt.problems.length).padStart(2, '0')}</b><span>Problems found</span></div><small className={tt.problems.length ? 'conflict-stat-active' : ''}>{tt.problems.length ? 'Needs a fix' : 'All clear'}</small></div>
      <div className="schedule-legend">{courses.map((c, i) => <span key={c}><i className={`legend-dot dot-${PALETTE[i % PALETTE.length]}`} /> {c.toUpperCase()}</span>)}</div>
    </div>

    <div className="schedule-workspace has-change-panel">
      <div className="panel timetable-panel">
        <div className="timetable-panel-top"><div><b>{me.user.role === 'trainer' ? 'Your schedule' : 'Institute-wide schedule'}</b><span><span className="live-pip" /> Live from the database</span></div></div>
        {me.user.role === 'trainer' && <div className="trainer-scope-banner"><ShieldCheck size={13} /> Showing only {me.user.display_name}'s classes.</div>}
        <div className="schedule-scroller"><div className="schedule-grid" style={{ '--day-count': '7' } as CSSProperties}>
          <div className="schedule-corner"><span>IST</span></div>
          {DAYS.map((day, i) => <div className={`day-header ${i === todayIndex ? 'today' : ''}`} key={day}><span>{day.slice(0, 3).toUpperCase()}</span><b>{dateOf(i)}</b>{i === todayIndex && <i>TODAY</i>}</div>)}
          {starts.map((start) => <div className="schedule-row" key={start}>
            <div className="time-label"><b>{api.hh(start)}</b><span>STARTS</span></div>
            {DAYS.map((day, di) => {
              const here = tt.slots.filter((s) => s.day_of_week === di && s.start_hour === start);
              const conflict = here.some((s) => s.has_problem);
              return <div key={`${day}-${start}`} className={`schedule-cell ${di === todayIndex ? 'today-column' : ''} ${conflict ? 'conflict-cell' : ''} ${here.length > 1 ? 'cell-stack' : ''}`}>
                {here.length === 0 && <div className="empty-slot" aria-hidden="true"><span>·</span></div>}
                {here.map((s) => <article key={s.id ?? `${s.batch_id}-${start}`} className={`schedule-event event-${tone(s)} ${s.has_problem ? 'event-conflict' : ''} ${changed.has(s.batch_code) ? 'event-affected' : ''}`} title={s.course_name}>
                  <div className="event-accent" />
                  <button className="event-main" onClick={() => openUnavailable(s)} aria-label={`${s.batch_code}, ${s.trainer_name}, ${s.room_name}`}>
                    <b>{s.batch_code} <span className="event-time">{api.hh(s.start_hour)}–{api.hh(s.end_hour)}</span></b>
                    <span className="event-batch">{s.course_name ? `${s.course_name} · ` : ''}{MODE[s.mode] ?? s.mode}{s.is_online && s.room_name !== 'Online' ? ' + online' : ''}</span>
                    <span className="event-trainer"><span className="event-trainer-avatar">{s.trainer_name.split(' ').map((p) => p[0]).join('')}</span>{s.trainer_name}</span>
                    <span className="event-room">{s.room_name}</span>
                  </button>
                  {s.has_problem && <span className="conflict-corner"><X size={10} /></span>}
                </article>)}
              </div>;
            })}
          </div>)}
        </div></div>
        <div className="timetable-panel-foot"><span><span className="live-pip" /> {isOwner ? 'Click a class to mark its trainer unavailable' : 'Only the owner can change the timetable'}</span><button onClick={load}>REFRESH <ArrowRight size={10} /></button></div>
      </div>

      <aside className={`panel change-list-panel ${plan ? 'change-list-live' : ''}`}>
        <div className="change-list-header"><div className="change-list-icon"><ArrowLeftRight size={16} /></div><div><b>Change list</b><span>{plan ? `${plan.proposal.changes.length} change(s) proposed` : 'Schedule impact, in context'}</span></div>{plan && <span className="change-count">{String(plan.proposal.changes.length).padStart(2, '0')}</span>}</div>
        {!plan ? <div className="change-empty">
          {tt.problems.length > 0 ? <>
            <div className="change-alert"><span className="alert-pulse" /><div><b>{tt.problems.length} problem(s) this week</b>{tt.problems.map((p) => <small key={p}>{p}</small>)}</div></div>
            {isOwner && <button onClick={() => void run(api.fixClashes())} disabled={busy}><WandSparkles size={13} /> Fix clashes</button>}
          </> : <>
            <span className="empty-orbit"><CalendarDays size={22} /></span><b>No changes yet</b>
            <p>Mark a trainer unavailable or add a batch to see the re-planned week here before anything is saved.</p>
            {isOwner && <button onClick={() => openUnavailable()} disabled={busy}><UserRoundX size={13} /> Mark trainer unavailable</button>}
          </>}
          <div className="change-empty-note"><ShieldCheck size={12} /> Nothing updates without human approval</div>
        </div> : <>
          <div className="change-alert"><span className="alert-pulse" /><div><b>{plan.action ? 'Proposed re-plan' : 'Nothing to change'}</b><small>{plan.message}</small></div></div>
          <div className="change-items-label"><span>PROPOSED CHANGES</span><span>{plan.proposal.changes.length} CHANGES</span></div>
          <div className="change-items">{plan.proposal.changes.map((c, i) => {
            const [day, time] = (c.to ?? c.from ?? '').split(' ');
            return <article className="change-item" key={i}>
              <div className="change-item-day"><span>{(day ?? '').toUpperCase()}</span><b>{(time ?? '').split('–')[0]}</b><i /></div>
              <div className="change-item-body"><b>{c.batch_code}</b><small>{c.reason}</small>
                <div className="change-swap"><span className="from-trainer"><i />{c.from ?? 'New class'}</span><ArrowRight size={12} /><span className="to-trainer"><i />{c.to ?? 'Removed'}</span></div>
              </div>
            </article>;
          })}</div>
          {plan.proposal.unscheduled.length > 0 && <div className="tt-unscheduled">Could not place: {plan.proposal.unscheduled.map((u) => `${u.batch_code} (${u.reason})`).join('; ')}</div>}
          <div className="ai-reasoning"><span className="ai-reasoning-icon"><Sparkles size={13} /></span><div><b>How it decided</b><p>Every class that still fits stays where it is. Broken ones move to the nearest slot where trainer, room and online licence are all free; a trainer with the same skill substitutes if needed. Solved in {plan.proposal.elapsed_ms} ms.</p></div></div>
          {plan.action ? <>
            <button className="push-approval-button" onClick={() => void approvePlan()} disabled={busy}><span>{busy ? 'Applying…' : 'Approve and apply'}</span><Check size={15} /></button>
            <div className="pushed-state"><button onClick={() => void discardPlan()} disabled={busy}><X size={12} /> Discard</button><button onClick={() => onNavigate('approvals')}>Open in Approvals <ArrowRight size={12} /></button></div>
          </> : <div className="pushed-state"><button onClick={() => setPlan(null)}>Close</button></div>}
          <div className="pending-approval-note"><ShieldCheck size={12} /> Saved only after approval · stale previews are refused</div>
        </>}
      </aside>
    </div>
    <div className="timetable-note"><CircleHelp size={13} /><span>Constraints: trainer availability and leave, room capacity, one class per trainer and room at a time, online licences, one class per batch per day.</span><span className="no-write-note">CHANGES SAVE ONLY AFTER APPROVAL</span></div>

    {dialog === 'unavailable' && <Modal eyebrow="RE-PLAN THE WEEK" title="Mark trainer unavailable" onClose={() => setDialog(null)}>
      <p className="modal-lede">Pick the trainer and the days they're away. The planner moves only the affected classes and explains each move. Nothing is saved until you approve.</p>
      <label className="field-label" htmlFor="trainer-choice">TRAINER</label>
      <div className="select-field"><select id="trainer-choice" value={pick.trainerId ?? ''} onChange={(e) => setPick({ ...pick, trainerId: Number(e.target.value) })}>{trainers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select><ChevronDown size={14} /></div>
      <span className="field-label">DAYS IN THE WEEK SHOWN ({api.fmtDay(tt.week_start)} – {api.fmtDay(tt.week_end)})</span>
      <div className="day-picks">{SHORT.map((d) => <label key={d} className={`day-pick ${pick.days.includes(d) ? 'on' : ''}`}><input type="checkbox" checked={pick.days.includes(d)} onChange={() => setPick({ ...pick, days: pick.days.includes(d) ? pick.days.filter((x) => x !== d) : [...pick.days, d] })} />{d}</label>)}</div>
      <label className="field-label" htmlFor="leave-reason">REASON</label>
      <input className="form-input" id="leave-reason" value={pick.reason} onChange={(e) => setPick({ ...pick, reason: e.target.value })} />
      <div className="simulate-summary"><span><Sparkles size={14} /></span><div><b>Stays a proposal</b><small>You'll see every change and its reason before anything is saved.</small></div></div>
      <div className="modal-actions"><button className="text-button" onClick={() => setDialog(null)}>Cancel</button><button className="simulate-button" disabled={busy || !pick.trainerId || pick.days.length === 0} onClick={() => void run(api.trainerUnavailable(pick.trainerId as number, pick.days, pick.reason || 'unavailable'))}><WandSparkles size={14} /> {busy ? 'Planning…' : 'Re-plan the week'}</button></div>
    </Modal>}
    {dialog === 'batch' && <BatchDialog trainers={trainers} busy={busy} onClose={() => setDialog(null)} onSubmit={(spec) => void run(api.addBatch(spec))} />}
  </section>;
}

const TIMES = Array.from({ length: 29 }, (_, i) => 7 + i / 2).map((h) => api.hh(h));   // 07:00 .. 21:00

function BatchDialog({ trainers, busy, onClose, onSubmit }: { trainers: Trainer[]; busy: boolean; onClose: () => void; onSubmit: (spec: Record<string, unknown>) => void }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [requests, setRequests] = useState<BatchRequest[]>([]);
  const [f, setF] = useState({ code: 'NEW-01', course_code: '', trainer_id: 0, mode: 'hybrid', days: ['Tue', 'Thu'] as string[], start_time: '17:00', session_hours: 2, classroom_size: 10, online_size: 4 });
  const [fromRequest, setFromRequest] = useState('');

  function applyRequest(r: BatchRequest | undefined) {
    if (!r) { setFromRequest(''); return; }
    setFromRequest(r.code);
    setF({ code: r.code, course_code: r.course_code, trainer_id: r.trainers[0]?.id ?? 0, mode: r.mode || 'hybrid',
           days: r.preferred_days.length ? r.preferred_days : ['Tue', 'Thu'], start_time: r.preferred_start || '17:00',
           session_hours: r.session_hours || 2, classroom_size: r.classroom_size, online_size: r.online_size });
  }
  useEffect(() => {
    api.getCourses().then((c) => { setCourses(c); setF((x) => (x.course_code ? x : { ...x, course_code: c[0]?.code ?? '' })); }).catch(() => undefined);
    api.getBatchRequests().then((r) => { setRequests(r); applyRequest(r.find((x) => !x.already_added)); }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const course = courses.find((c) => c.code === f.course_code);
  const skilled = trainers.filter((t) => api.teaches(t.skills, course));
  const trainerId = skilled.some((t) => t.id === f.trainer_id) ? f.trainer_id : skilled[0]?.id ?? 0;
  const set = (k: keyof typeof f, numeric = false) => (e: { target: { value: string } }) => setF({ ...f, [k]: numeric ? Number(e.target.value) : e.target.value });
  const toggleDay = (d: string) => setF({ ...f, days: f.days.includes(d) ? f.days.filter((x) => x !== d) : SHORT.filter((x) => x === d || f.days.includes(x)) });
  const online = f.mode === 'classroom' ? 0 : f.online_size;
  const classroom = f.mode === 'online' ? 0 : f.classroom_size;

  return <Modal eyebrow="ADD TO THE TIMETABLE" title="Add a batch" onClose={onClose}>
    <p className="modal-lede">The planner tries the days and time you ask for, keeps every existing class where it is, and explains where each new class goes.</p>
    {requests.length > 0 && <>
      <label className="field-label" htmlFor="nb-request">START FROM A REQUEST</label>
      <div className="select-field"><select id="nb-request" value={fromRequest} onChange={(e) => applyRequest(requests.find((r) => r.code === e.target.value))}>
        <option value="">Enter details by hand</option>
        {requests.map((r) => <option key={r.code} value={r.code} disabled={r.already_added}>{r.code} · {r.course_name} · {r.expected_students} students ({r.online_seats_needed} online) · {r.preferred_days.join('/')} {r.preferred_start}{r.already_added ? ' · added' : ''}</option>)}
      </select><ChevronDown size={14} /></div>
    </>}
    <div className="form-grid">
      <div><label className="field-label" htmlFor="nb-code">BATCH CODE</label><input className="form-input" id="nb-code" value={f.code} onChange={set('code')} /></div>
      <div style={{ gridColumn: 'span 2' }}><label className="field-label" htmlFor="nb-course">COURSE</label>
        <div className="select-field"><select id="nb-course" value={f.course_code} onChange={set('course_code')}>{courses.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select><ChevronDown size={14} /></div></div>
    </div>
    <label className="field-label" htmlFor="nb-trainer">TRAINER (WHO TEACHES THIS COURSE)</label>
    <div className="select-field"><select id="nb-trainer" value={trainerId} onChange={set('trainer_id', true)}>{skilled.length === 0 && <option value={0}>No trainer has this skill</option>}{skilled.map((t) => <option key={t.id} value={t.id}>{t.name} · {t.skills.split(',').join(', ')}</option>)}</select><ChevronDown size={14} /></div>
    <span className="field-label">PREFERRED DAYS</span>
    <div className="day-picks">{SHORT.map((d) => <label key={d} className={`day-pick ${f.days.includes(d) ? 'on' : ''}`}><input type="checkbox" checked={f.days.includes(d)} onChange={() => toggleDay(d)} />{d}</label>)}</div>
    <div className="form-grid">
      <div><label className="field-label" htmlFor="nb-start">START TIME</label><div className="select-field"><select id="nb-start" value={f.start_time} onChange={set('start_time')}>{TIMES.map((t) => <option key={t} value={t}>{t}</option>)}</select><ChevronDown size={14} /></div></div>
      <div><label className="field-label" htmlFor="nb-hours">HOURS PER CLASS</label><div className="select-field"><select id="nb-hours" value={f.session_hours} onChange={set('session_hours', true)}>{[1, 1.5, 2, 2.5, 3].map((h) => <option key={h} value={h}>{h}</option>)}</select><ChevronDown size={14} /></div></div>
      <div><label className="field-label" htmlFor="nb-mode">MODE</label><div className="select-field"><select id="nb-mode" value={f.mode} onChange={set('mode')}><option value="classroom">Classroom</option><option value="online">Online</option><option value="hybrid">Hybrid</option></select><ChevronDown size={14} /></div></div>
    </div>
    <div className="form-grid">
      <div><label className="field-label" htmlFor="nb-class">CLASSROOM STUDENTS</label><input className="form-input" id="nb-class" type="number" min={0} max={60} value={classroom} disabled={f.mode === 'online'} onChange={set('classroom_size', true)} /></div>
      <div><label className="field-label" htmlFor="nb-online">ONLINE STUDENTS</label><input className="form-input" id="nb-online" type="number" min={0} max={60} value={online} disabled={f.mode === 'classroom'} onChange={set('online_size', true)} /></div>
      <div><span className="field-label">CLASSES / WEEK</span><div className="form-static">{f.days.length || 2}</div></div>
    </div>
    <div className="modal-actions"><button className="text-button" onClick={onClose}>Cancel</button><button className="simulate-button" disabled={busy || !f.code.trim() || !trainerId || !course} onClick={() => onSubmit({
      code: f.code.trim(), course_code: f.course_code, trainer_id: trainerId, mode: f.mode,
      preferred_days: f.days.length ? f.days : undefined, sessions_per_week: f.days.length || 2,
      start_time: f.start_time, session_hours: f.session_hours,
      classroom_size: classroom, online_size: online, online_seat_limit: online,
    })}><WandSparkles size={14} /> {busy ? 'Planning…' : 'Find slots'}</button></div>
  </Modal>;
}
