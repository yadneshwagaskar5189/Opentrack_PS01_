import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, ArrowDownToLine, ChevronDown, ChevronRight, Clipboard, Clock3, FileJson2, Fingerprint, RefreshCw, Search, Shield, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { AuditEntry, AuditVerify } from '../types';
import * as api from '../services/api';
import type { ViewProps } from './common';

type Status = 'Success' | 'Review' | 'Blocked';
const LABEL: Record<string, string> = {
  query_ok: 'Answered a question', query_blocked: 'Blocked by the database', refusal: 'Refused by the copilot',
  ask_action: 'Drafted a preview', action_proposed: 'Proposed an action', action_approved: 'Approved an action',
  action_rejected: 'Rejected an action', action_failed: 'Action failed', query_retry: 'Retried a query',
  error: 'Error', login: 'Signed in', login_failed: 'Failed sign-in', seed: 'Demo data reset',
  reconciliation_viewed: 'Viewed fee reconciliation', audit_verified: 'Checked the audit seals',
  whatsapp_delivery: 'WhatsApp delivery', follow_up_skipped: 'Follow-up skipped',
};
function statusOf(t: string): Status {
  if (['query_blocked', 'login_failed', 'error', 'action_failed'].includes(t)) return 'Blocked';
  if (['refusal', 'query_retry', 'action_proposed', 'ask_action', 'action_rejected'].includes(t)) return 'Review';
  return 'Success';
}
function targetOf(e: AuditEntry) {
  const d = e.detail ?? {};
  if (e.question) return e.question;
  if (typeof d.title === 'string') return d.title;
  if (typeof d.action_id === 'number') return `ACT-${d.action_id}${typeof d.action_type === 'string' ? ` · ${d.action_type.replaceAll('_', ' ')}` : ''}`;
  return e.outcome ?? '';
}

export function AuditLogView({ me, onToast }: ViewProps) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<'All events' | Status>('All events');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [seal, setSeal] = useState<AuditVerify | null>(null);
  const [checking, setChecking] = useState(false);

  async function verify() {
    setChecking(true);
    try { const v = await api.verifyAudit(); setSeal(v); onToast(v.ok ? 'Audit log intact' : 'Audit log was changed', v.ok ? 'success' : 'error'); load(); }
    catch (e) { onToast((e as Error).message, 'error'); }
    finally { setChecking(false); }
  }

  const load = useCallback(() => {
    setError('');
    api.getAudit(300).then(setEntries).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const all = entries ?? [];
  const filtered = useMemo(() => all.filter((e) => (filter === 'All events' || statusOf(e.event_type) === filter) &&
    `${e.id} ${e.username ?? ''} ${e.role ?? ''} ${LABEL[e.event_type] ?? e.event_type} ${targetOf(e)} ${e.sql_text ?? ''}`.toLowerCase().includes(search.toLowerCase())), [all, filter, search]);
  const blocked = all.filter((e) => statusOf(e.event_type) === 'Blocked').length;
  const ok = all.filter((e) => statusOf(e.event_type) === 'Success').length;

  function exportCsv() {
    const esc = (v: unknown) => `"${String(v ?? '').replaceAll('"', '""')}"`;
    const rows = filtered.map((e) => [e.id, e.ts, e.username, e.role, e.event_type, targetOf(e), e.sql_text].map(esc).join(','));
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([['id,time,user,role,event,target,sql', ...rows].join('\n')], { type: 'text/csv' }));
    link.download = 'audit-log.csv';
    link.click();
    URL.revokeObjectURL(link.href);
    onToast('Audit log exported as CSV', 'success');
  }

  return <section className="view-page audit-view">
    <div className="page-heading audit-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><Shield size={12} /></span> TRUST IS A TRACEABLE SYSTEM</div><h1>Audit <span>trail</span></h1><p>{me.user.role === 'owner' ? 'Every question, query, refusal and approval, by everyone.' : 'Your own questions, queries and decisions.'}</p></div><button className="outline-action audit-export" onClick={exportCsv} disabled={!filtered.length}><ArrowDownToLine size={14} /> Export view</button></div>
    <div className="audit-overview"><div className="audit-health"><span className="audit-health-mark"><Activity size={18} /></span><div><small>GOVERNANCE FEED</small><b>Database guard <span>active</span></b><span className="audit-health-caption"><i /> Every copilot query is checked and logged</span></div></div><div className="audit-stat"><b>{String(all.length).padStart(2, '0')}</b><span>EVENTS<br />IN VIEW</span></div><div className="audit-stat success-stat"><b>{String(ok).padStart(2, '0')}</b><span>ANSWERED<br />OR APPROVED</span></div><div className="audit-stat blocked-stat"><b>{String(blocked).padStart(2, '0')}</b><span>REQUESTS<br />BLOCKED</span></div>{me.user.role === 'owner' && <button className="audit-window audit-verify" onClick={() => void verify()} disabled={checking}><Fingerprint size={14} /><span>{checking ? 'CHECKING…' : 'VERIFY SEALS'}</span></button>}<button className="audit-window" onClick={load}><RefreshCw size={14} /><span>REFRESH</span></button></div>
    {seal && <div className={`seal-banner ${seal.ok ? 'seal-ok' : 'seal-bad'}`} role="status">{seal.ok ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}<div><b>{seal.ok ? 'Audit log intact' : 'Audit log was changed'}</b><span>{seal.message}{seal.last_entry ? ` Newest entry: EVT-${seal.last_entry.id}.` : ''}</span>{!seal.ok && <ul>{seal.problems.slice(0, 5).map((p) => <li key={p.id}>EVT-{p.id} {p.problem}</li>)}</ul>}<small>Each entry is sealed with a keyed hash of its content and the entry before it. The key is kept outside the database.</small></div></div>}
    <div className="audit-controls"><div className="filter-tabs audit-filters" role="tablist" aria-label="Filter audit events">{(['All events', 'Success', 'Review', 'Blocked'] as const).map((s) => <button key={s} className={filter === s ? 'selected' : ''} onClick={() => setFilter(s)} role="tab" aria-selected={filter === s}>{s}{s === 'Blocked' && blocked > 0 && <span className="blocked-count">{blocked}</span>}</button>)}</div><label className="audit-search"><Search size={14} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search user, question, SQL…" /></label></div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div>}
    <div className="panel audit-table-panel"><div className="audit-table-header"><span className="audit-row-toggle" /><span>TIMESTAMP</span><span>ACTOR</span><span>ACTION &amp; TARGET</span><span>STATUS</span><span>EVENT ID</span></div>
      <div className="audit-date-divider"><span>MOST RECENT FIRST</span><i /><span>{filtered.length} EVENTS</span></div>
      {!entries && !error && <div className="audit-empty"><span className="live-pip" /> Loading the audit trail…</div>}
      {filtered.map((e) => {
        const status = statusOf(e.event_type);
        const name = e.username ?? 'system';
        return <div key={e.id} className={`audit-entry ${expanded === e.id ? 'audit-expanded' : ''}`}>
          <button className="audit-row" onClick={() => setExpanded((c) => (c === e.id ? null : e.id))} aria-expanded={expanded === e.id}>
            <span className="audit-row-toggle">{expanded === e.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
            <span className="audit-time">{api.fmtTime(e.ts)}<small>{api.fmtDay(e.ts)}</small></span>
            <span className="audit-actor"><span className={`actor-avatar ${status === 'Blocked' ? 'actor-system' : ''}`}>{status === 'Blocked' ? <ShieldAlert size={13} /> : name.slice(0, 2).toUpperCase()}</span><span><b>{name}</b><small>{e.role ?? 'system'}</small></span></span>
            <span className="audit-action-copy"><b>{LABEL[e.event_type] ?? e.event_type}</b><small>{targetOf(e)}</small></span>
            <span className={`audit-status audit-${status.toLowerCase()}`}>{status === 'Success' ? <ShieldCheck size={12} /> : status === 'Blocked' ? <ShieldAlert size={12} /> : <Clock3 size={12} />}{status}</span>
            <span className="audit-event-id">EVT-{e.id}</span>
          </button>
          {expanded === e.id && <div className="payload-inspector">
            <div className="payload-header"><span><FileJson2 size={13} /> EVENT DETAIL</span><button onClick={async (ev) => { ev.stopPropagation(); try { await navigator.clipboard.writeText(JSON.stringify(e, null, 2)); onToast('Event copied', 'success'); } catch { onToast('Clipboard unavailable', 'warning'); } }}><Clipboard size={12} /> Copy JSON</button></div>
            <pre>{JSON.stringify({ outcome: e.outcome, sql: e.sql_text ?? undefined, ...(e.detail ?? {}), seal: e.hash ? `${e.hash.slice(0, 16)}…` : undefined }, null, 2)}</pre>
            <div className="payload-meta"><span>ACTOR ROLE / {(e.role ?? 'system').toUpperCase()}</span><span>APPEND-ONLY · EVT-{e.id}</span></div>
          </div>}
        </div>;
      })}
      {entries && filtered.length === 0 && <div className="audit-empty"><Search size={17} /> No events match those filters.</div>}
      <div className="audit-pagination"><span>Showing <b>{filtered.length}</b> of <b>{all.length}</b> events</span><span><ShieldCheck size={12} /> Read-only audit view</span></div>
    </div>
    <div className="audit-footnote"><ShieldCheck size={14} /><span>THE AUDIT LOG IS APPEND-ONLY. BLOCKED ATTEMPTS ARE RECORDED TOO.</span><span>OWNER SEES EVERYONE · OTHERS SEE THEIR OWN</span></div>
  </section>;
}
