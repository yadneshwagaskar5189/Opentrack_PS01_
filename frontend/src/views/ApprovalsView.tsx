import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowDown, Check, Clock3, FilePenLine, GitCompareArrows, MessageCircle, RefreshCw, ShieldCheck, Wallet, X, XCircle } from 'lucide-react';
import type { Action } from '../types';
import * as api from '../services/api';
import { Modal } from '../components/Feedback';
import { ActionPreview, ChangeList, resultText, WaSendList } from '../components/ActionPreview';
import { ACTION_LABEL, type ViewProps } from './common';

const FILTERS = ['Pending', 'All', 'Approved', 'Rejected', 'Failed'] as const;
type Filter = typeof FILTERS[number];
const STATUS_LABEL: Record<string, string> = { pending: 'Pending', approved: 'Approved', rejected: 'Rejected', failed: 'Failed' };

// Turns a backend action into the before/after story the card shows.
function describe(a: Action) {
  const items = a.payload.items ?? [];
  const pv = a.payload.preview;
  if (pv) {
    return { summary: pv.summary, before: 'Not saved yet', after: pv.fields.slice(0, 3).map(([k, v]) => `${k}: ${v}`).join('  ·  '),
             rationale: [...pv.checks, ...pv.warnings].join(' · '), priority: pv.warnings.length ? 'High' : 'Normal' };
  }
  if (a.action_type === 'send_whatsapp') {
    const names = items.map((i) => i.name).join(', ');
    return { summary: `${items.length} WhatsApp message(s) to ${names}.`, before: 'Not sent', after: `Send to ${names}`,
             rationale: items[0]?.message ?? '', priority: 'Normal' };
  }
  if (a.action_type === 'mark_instalment_paid') {
    const total = items.reduce((s, i) => s + (i.balance ?? 0), 0);
    return { summary: `${items.length} instalment(s): ${items.map((i) => i.student_name).join(', ')}.`,
             before: `Unpaid · ${api.rupees(total)} outstanding`, after: `Paid · payment recorded`,
             rationale: items.map((i) => `${i.student_name}: ${api.rupees(i.balance)} due ${api.fmtDate(i.due_date)}`).join('; '), priority: 'Normal' };
  }
  const changes = a.payload.changes ?? [];
  return { summary: `${changes.length} class(es) affected. Planned in ${a.payload.elapsed_ms ?? '–'} ms.`,
           before: changes.map((c) => `${c.batch_code} ${c.from ?? '(new)'}`).join('  /  ') || 'Current timetable',
           after: changes.map((c) => `${c.batch_code} ${c.to ?? '(removed)'}`).join('  /  ') || 'No changes',
           rationale: changes[0]?.reason ?? '', priority: 'High' };
}

export function ApprovalsView({ me, onToast, refreshPending }: ViewProps) {
  const [actions, setActions] = useState<Action[] | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<Filter>('Pending');
  const [reviewing, setReviewing] = useState<Action | null>(null);
  const [rejecting, setRejecting] = useState<Action | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError('');
    api.listActions().then(setActions).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const filtered = useMemo(() => (actions ?? []).filter((a) => filter === 'All' || STATUS_LABEL[a.status] === filter), [actions, filter]);
  const pendingCount = (actions ?? []).filter((a) => a.status === 'pending').length;
  const today = new Date().toDateString();
  const approvedToday = (actions ?? []).filter((a) => a.status === 'approved' && a.decided_at && new Date(a.decided_at).toDateString() === today).length;
  const failed = (actions ?? []).filter((a) => a.status === 'failed').length;
  const canDecide = (a: Action) => me.allowed_actions.includes(a.action_type);

  function decided() { load(); refreshPending(); }

  async function quickApprove(a: Action) {
    setBusy(true);
    try {
      await api.approveAction(a.id);
      onToast('Approved · decision recorded in the audit log', 'success');
      decided();
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }
  async function saveReject() {
    if (!rejecting) return;
    setBusy(true);
    try {
      await api.rejectAction(rejecting.id, rejectReason.trim());
      onToast('Rejected with an audit note', 'warning');
      setRejecting(null); setRejectReason('');
      decided();
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  return <section className="view-page approvals-view">
    <div className="page-heading approvals-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><GitCompareArrows size={12} /></span> HUMAN OVERSIGHT · EVERY CHANGE EXPLAINED</div><h1>Approval <span>queue</span></h1><p>Messages the copilot drafted and changes it proposed. Nothing is sent or saved until someone with the right role approves it.</p></div><div className="approval-heading-stat"><b>{String(pendingCount).padStart(2, '0')}</b><span>AWAITING<br />YOUR REVIEW</span><span className="stat-pulse" /></div></div>
    <div className="approval-overview"><div className="overview-label"><span className="overview-icon"><ShieldCheck size={15} /></span><span><b>Decision desk</b><small>Nothing goes live without an authorized human</small></span></div><div className="overview-metrics"><div><b>{String(pendingCount).padStart(2, '0')}</b><span>Pending</span></div><div><b>{String(approvedToday).padStart(2, '0')}</b><span>Approved today</span></div><div><b>{String(failed).padStart(2, '0')}</b><span>Failed</span></div></div><div className="decision-authority"><span className="authority-indicator" /><span>{me.allowed_actions.length ? `You can approve: ${me.allowed_actions.map((t) => ACTION_LABEL[t] ?? t).join(', ')}` : 'Read-only review'}</span></div></div>
    <div className="approval-toolbar"><div className="filter-tabs" role="tablist" aria-label="Filter approvals">{FILTERS.map((item) => <button key={item} className={filter === item ? 'selected' : ''} onClick={() => setFilter(item)} role="tab" aria-selected={filter === item}>{item}{item === 'Pending' && <span>{pendingCount}</span>}</button>)}</div><button className="toolbar-quiet" onClick={load}><RefreshCw size={13} /> REFRESH</button></div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div>}
    {!actions && !error && <div className="loading-row"><span className="live-pip" /> Loading the approval queue…</div>}
    <div className="approval-grid">
      {filtered.map((a, index) => {
        const d = describe(a);
        const initials = (a.created_by_name ?? 'System').split(' ').map((n) => n[0]).join('').slice(0, 2);
        return <article className={`panel approval-card ${a.status !== 'pending' ? 'resolved-card' : ''}`} key={a.id} style={{ animationDelay: `${index * 55}ms` }}>
          <div className="approval-card-head"><div className="request-id"><span className="id-mark">{a.action_type === 'apply_timetable' ? <Clock3 size={13} /> : a.action_type === 'send_whatsapp' ? <MessageCircle size={13} /> : <Wallet size={13} />}</span><span>ACT-{a.id}</span><span className="id-sep">/</span><span>{ACTION_LABEL[a.action_type]}</span></div></div>
          <div className="approval-title-row"><div><h3>{a.title}</h3><p>{d.summary}</p></div><span className={`priority-tag priority-${d.priority.toLowerCase()}`}><i />{d.priority}</span></div>
          <div className="diff-block"><div className="diff-label"><span><GitCompareArrows size={12} /> PROPOSED CHANGE</span><span className="diff-lines">BEFORE <span className="diff-arrow-thin">→</span> AFTER</span></div><div className="diff-values"><div className="diff-old"><span className="diff-dot old-dot" /><span>{d.before}</span></div><span className="diff-arrow"><ArrowDown size={13} /></span><div className="diff-new"><span className="diff-dot new-dot" /><span>{d.after}</span></div></div></div>
          {d.rationale && <div className="rationale"><span className="rationale-mark">“</span><p>{d.rationale}</p></div>}
          <div className="request-footer"><div className="requester-avatar">{initials}</div><div className="requester-info"><b>{a.created_by_name ?? 'System'}</b><small>Proposed <span>·</span> {api.fmtDay(a.created_at)} {api.fmtTime(a.created_at)}</small></div><span className={`status-pill ${a.status === 'pending' ? 'status-review' : a.status === 'approved' ? 'status-ok' : 'status-bad'}`}><i />{STATUS_LABEL[a.status]}</span></div>
          {a.status === 'pending' && <div className="approval-actions">{canDecide(a) ? <>
            {a.action_type === 'apply_timetable'
              ? <button className="approve-action" disabled={busy} onClick={() => void quickApprove(a)}><Check size={14} /> Approve and apply</button>
              : <button className="approve-action" onClick={() => setReviewing(a)}><Check size={14} /> Review and approve</button>}
            <button className="edit-action" onClick={() => setReviewing(a)}><FilePenLine size={13} /> {a.action_type === 'send_whatsapp' ? 'Edit messages' : 'Details'}</button>
            <button className="reject-action" onClick={() => setRejecting(a)}><XCircle size={14} /> Reject</button>
          </> : <span className="readonly-note"><ShieldCheck size={13} /> Your role can see this; only a role that may {ACTION_LABEL[a.action_type].toLowerCase()} can decide.</span>}</div>}
          {a.status === 'approved' && <div className="resolved-note"><Check size={12} /> {resultText(a)} <span>· Audit trail updated</span></div>}
          <WaSendList action={a} />
          {a.status === 'rejected' && <div className="resolved-note"><X size={12} /> Rejected{(a.result as { reason?: string } | null)?.reason ? `: ${(a.result as { reason?: string }).reason}` : ''} <span>· Nothing was sent or changed</span></div>}
          {a.status === 'failed' && <div className="resolved-note"><X size={12} /> {String((a.result as { error?: string } | null)?.error ?? 'Failed')} <span>· Nothing was changed</span></div>}
        </article>;
      })}
    </div>
    {actions && filtered.length === 0 && <div className="empty-state panel"><span><Check size={20} /></span><b>Nothing in this queue.</b><p>Ask the copilot to draft reminders or re-plan the timetable, and the previews appear here for approval.</p></div>}
    <div className="approval-footnote"><ShieldCheck size={13} /><span>APPROVALS ARE CLAIMED ONCE. A SECOND CLICK, OR A STALE TIMETABLE PREVIEW, IS REFUSED.</span><span>EVERY DECISION IS AUDITED</span></div>

    {reviewing && <Modal eyebrow={`ACT-${reviewing.id} · ${ACTION_LABEL[reviewing.action_type].toUpperCase()}`} title="Review before approving" size="wide" onClose={() => { setReviewing(null); load(); }}>
      <p className="modal-lede">{reviewing.action_type === 'send_whatsapp' ? 'Edit any message or untick a recipient. Approving logs the messages to the outbox.' : 'Check the details, then approve or reject.'}</p>
      <ActionPreview action={reviewing} canDecide={canDecide(reviewing)} onToast={onToast} onDecided={decided} />
    </Modal>}
    {rejecting && <Modal eyebrow={`ACT-${rejecting.id} · DECISION NOTE`} title="Reject this proposal" onClose={() => setRejecting(null)}>
      <p className="modal-lede">A short reason is saved with the decision in the audit log. Nothing is sent or changed.</p>
      {rejecting.action_type === 'apply_timetable' && <ChangeList proposal={rejecting.payload} />}
      <label className="field-label" htmlFor="reject-reason">REASON</label>
      <textarea className="form-textarea" id="reject-reason" autoFocus value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} rows={3} placeholder="Why is this being rejected?" />
      <div className="modal-actions"><button className="text-button" onClick={() => setRejecting(null)}>Cancel</button><button className="reject-action filled-reject" disabled={busy} onClick={() => void saveReject()}><X size={14} /> Reject proposal</button></div>
    </Modal>}
  </section>;
}

