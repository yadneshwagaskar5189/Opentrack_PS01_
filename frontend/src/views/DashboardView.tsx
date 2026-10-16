import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ArrowUpRight, CalendarDays, ChevronDown, ChevronRight, ClipboardCheck, Copy, GraduationCap, LayoutDashboard, MessageCircle, RotateCcw, Sparkles, Sunrise, TriangleAlert, Users, Wallet, Zap } from 'lucide-react';
import type { Brief, Dashboard } from '../types';
import * as api from '../services/api';
import type { ViewProps } from './common';

type Tile = { label: string; value: string; sub?: string; icon: ReactNode; tone: string; onClick?: () => void };

export function DashboardView({ me, onToast, onNavigate, askCopilot, refreshPending, institute }: ViewProps & { institute?: string }) {
  const [d, setD] = useState<Dashboard | null>(null);
  const [error, setError] = useState('');
  const [resetting, setResetting] = useState(false);
  const role = me.user.role;

  const load = useCallback(() => {
    setError('');
    api.getDashboard().then(setD).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function reset() {
    setResetting(true);
    try {
      await api.resetDemo();
      onToast('Demo data restored. Signing you back in…', 'success');
      await api.login(role);
      load();
      refreshPending();
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setResetting(false);
    }
  }

  const f = d?.fees;
  let tiles: Tile[] = [];
  if (d && (role === 'owner' || role === 'counselor')) {
    tiles = [
      { label: 'Active students', value: String(d.active_students ?? 0), sub: `${d.active_batches ?? 0} batches`, icon: <Users size={15} />, tone: 'stat-violet', onClick: () => onNavigate('students') },
      { label: 'EMIs due this week', value: api.rupees(f?.due_this_week.amount), sub: `${f?.due_this_week.count ?? 0} instalments`, icon: <Wallet size={15} />, tone: 'stat-green', onClick: () => askCopilot('Which EMIs are due this week?') },
      { label: 'Overdue fees', value: api.rupees(f?.overdue.amount), sub: `${f?.overdue.count ?? 0} instalments`, icon: <TriangleAlert size={15} />, tone: 'stat-orange', onClick: () => askCopilot('Show overdue EMIs, biggest first') },
      { label: 'Students at risk', value: String(d.at_risk?.high ?? 0), sub: `high, plus ${d.at_risk?.medium ?? 0} medium`, icon: <GraduationCap size={15} />, tone: 'stat-orange', onClick: () => onNavigate('risk') },
      { label: 'Timetable problems', value: String(d.timetable_problems ?? 0), sub: d.timetable_problems ? 'Open the timetable to fix' : 'No clashes', icon: <Zap size={15} />, tone: d.timetable_problems ? 'stat-orange' : 'stat-green', onClick: () => onNavigate('timetable') },
      { label: 'Waiting for approval', value: String(d.pending_actions), sub: 'Drafts and proposals', icon: <ClipboardCheck size={15} />, tone: 'stat-violet', onClick: () => onNavigate('approvals') },
    ];
  } else if (d && role === 'accounts') {
    tiles = [
      { label: 'EMIs due this week', value: api.rupees(f?.due_this_week.amount), sub: `${f?.due_this_week.count ?? 0} instalments`, icon: <Wallet size={15} />, tone: 'stat-violet', onClick: () => askCopilot('Which EMIs are due this week?') },
      { label: 'Overdue fees', value: api.rupees(f?.overdue.amount), sub: `${f?.overdue.count ?? 0} instalments`, icon: <TriangleAlert size={15} />, tone: 'stat-orange', onClick: () => askCopilot('Show overdue EMIs, biggest first') },
      { label: 'Collected this month', value: api.rupees(f?.collected_this_month), sub: 'All payment methods', icon: <Wallet size={15} />, tone: 'stat-green' },
      { label: 'Waiting for approval', value: String(d.pending_actions), sub: 'Drafts and fee updates', icon: <ClipboardCheck size={15} />, tone: 'stat-violet', onClick: () => onNavigate('approvals') },
    ];
  } else if (d) {
    tiles = [
      ...(d.my_batches ?? []).map((b) => ({ label: b.code, value: String(b.students), sub: `students · ${b.mode}`, icon: <Users size={15} />, tone: 'stat-violet', onClick: () => onNavigate('students') })),
      { label: 'Attendance, last 14 days', value: d.attendance_last_14_days_pct != null ? `${d.attendance_last_14_days_pct}%` : '–', sub: 'Across your batches', icon: <CalendarDays size={15} />, tone: 'stat-green', onClick: () => askCopilot('How is attendance in my batches?') },
      { label: 'Students at risk', value: String(d.at_risk?.high ?? 0), sub: `high, plus ${d.at_risk?.medium ?? 0} medium`, icon: <GraduationCap size={15} />, tone: 'stat-orange', onClick: () => onNavigate('risk') },
    ];
  }

  const quick: Record<string, string[]> = {
    owner: ['Which students in the Java batch have missed three or more classes and have an EMI due this week? Draft a WhatsApp reminder for each.', 'Is any trainer double-booked this week?'],
    counselor: ['Which students in the Java batch have missed three or more classes and have an EMI due this week? Draft a WhatsApp reminder for each.', 'Which B08 students are eligible for a certificate?'],
    accounts: ['Which EMIs are due this week?', "Mark Neha Verma's EMI as paid"],
    trainer: ['Who missed the most classes in my batches?', 'What is my timetable this week?'],
  };

  return <section className="view-page dashboard-view">
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><LayoutDashboard size={12} /></span> TODAY AT {(institute ?? 'THE INSTITUTE').toUpperCase()} · {api.fmtDay(d?.today)}</div><h1>Good to see you, <span>{me.user.display_name.split(' ')[0]}.</span></h1><p>{role === 'trainer' ? 'Your batches, your classes today and the students who need attention.' : role === 'accounts' ? 'Fees due, overdue and collected, with one click to the details.' : 'The institute at a glance. Click any number to dig in.'}</p></div>
      {role === 'owner' && <div className="heading-meta"><button className="outline-action" onClick={() => void reset()} disabled={resetting}><RotateCcw size={13} /> {resetting ? 'Resetting…' : 'Reset demo data'}</button></div>}
    </div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div>}
    {!d && !error && <div className="loading-row"><span className="live-pip" /> Loading today's numbers…</div>}
    <MorningBrief askCopilot={askCopilot} onToast={onToast} refreshKey={d?.today ?? ''} />
    {d && <>
      <div className={`dash-tiles cols-${Math.min(tiles.length, tiles.length === 4 ? 4 : 3)}`}>
        {tiles.map((t) => <button key={t.label} className={`panel dash-tile ${t.onClick ? 'clickable' : ''}`} onClick={t.onClick} disabled={!t.onClick}>
          <span className={`schedule-stat-icon ${t.tone}`}>{t.icon}</span>
          <span className="dash-tile-copy"><small>{t.label}</small><b>{t.value}</b>{t.sub && <span>{t.sub}</span>}</span>
          {t.onClick && <ArrowUpRight size={14} className="dash-tile-arrow" />}
        </button>)}
      </div>
      <div className="dash-columns">
        <div className="panel dash-panel">
          <div className="panel-top"><div className="results-heading"><span className="results-icon"><CalendarDays size={16} /></span><div><b>{role === 'trainer' ? 'Your classes today' : "Today's classes"}</b><small>{(d.classes_today ?? []).length} SCHEDULED</small></div></div></div>
          {(d.classes_today ?? []).length === 0 ? <p className="dash-empty">No classes today.</p> : <ul className="dash-classes">
            {(d.classes_today ?? []).map((c, i) => <li key={i}><span className="mono-cell">{api.hh(c.start_hour)}–{api.hh(c.end_hour)}</span><b>{c.batch_code}</b><span>{c.trainer_name}</span><span className="muted-cell">{c.room_name ?? 'Online'}{c.is_online && c.room_name ? ' + online' : ''}{c.trainer_on_leave ? ' · trainer on leave' : ''}</span></li>)}
          </ul>}
        </div>
        <div className="panel dash-panel">
          <div className="panel-top"><div className="results-heading"><span className="results-icon"><Sparkles size={16} /></span><div><b>Ask the copilot</b><small>ONE CLICK, WITH THE QUERY SHOWN</small></div></div></div>
          <div className="dash-asks">{quick[role].map((q) => <button key={q} className="prompt-chip" onClick={() => askCopilot(q)}>{q}<ArrowUpRight size={12} /></button>)}</div>
          {role === 'accounts' && d.collections_by_method && d.collections_by_method.length > 0 && <table className="data-table compact-table"><thead><tr><th>Method</th><th>Payments</th><th>Amount</th></tr></thead><tbody>{d.collections_by_method.map((m) => <tr key={m.method}><td>{m.method.replace('_', ' ')}</td><td>{m.payments}</td><td>{api.rupees(m.amount)}</td></tr>)}</tbody></table>}
        </div>
      </div>
    </>}
  </section>;
}

const BRIEF_KEY = 'ic_brief_open';

// Today at a glance for this role (GET /api/brief): copy it, or send it to yourself on WhatsApp.
function MorningBrief({ askCopilot, onToast, refreshKey }: { askCopilot: (q: string) => void; onToast: ViewProps['onToast']; refreshKey: string }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(BRIEF_KEY) !== '0'; } catch { return true; } });
  useEffect(() => { api.getBrief().then(setBrief).catch(() => setBrief(null)); }, [refreshKey]);
  if (!brief) return null;
  const toggle = () => setOpen((o) => { try { localStorage.setItem(BRIEF_KEY, o ? '0' : '1'); } catch { /* fine */ } return !o; });
  async function copy() {
    try { await navigator.clipboard.writeText(brief?.text ?? ''); onToast('Brief copied', 'success'); }
    catch { onToast('Clipboard unavailable in this browser', 'warning'); }
  }
  const warn = brief.sections.filter((s) => s.tone === 'warn').length;
  return <div className="panel brief-panel">
    <div className="brief-top">
      <button className="brief-toggle" onClick={toggle} aria-expanded={open}>
        <span className="brief-icon"><Sunrise size={16} /></span>
        <span><b>Morning brief</b><small>{brief.headline}{warn ? ` ${warn} thing(s) need attention.` : ' All calm.'}</small></span>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      </button>
      <div className="brief-actions">
        <button className="outline-action" onClick={() => void copy()}><Copy size={13} /> Copy</button>
        <a className="outline-action brief-wa" href={`https://wa.me/?text=${encodeURIComponent(brief.text)}`} target="_blank" rel="noreferrer"><MessageCircle size={13} /> Send on WhatsApp</a>
      </div>
    </div>
    {open && <div className="brief-grid">
      {brief.sections.map((s) => <section key={s.key} className={`brief-card brief-${s.tone}`}>
        <h4><i />{s.title}</h4>
        <ul>{s.lines.map((l) => <li key={l}>{l}</li>)}</ul>
        {s.ask && <button className="text-button brief-ask" onClick={() => askCopilot(s.ask as string)}>Ask the copilot <ArrowUpRight size={12} /></button>}
      </section>)}
    </div>}
  </div>;
}
