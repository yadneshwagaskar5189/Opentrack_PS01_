import { useState } from 'react';
import { ArrowRight, Award, Check, ChevronDown, CircleAlert, FileText, MessageCircle, ShieldCheck, XCircle } from 'lucide-react';
import type { Action, EntryPreview, Proposal } from '../types';
import { ENTRY_TYPES } from '../types';
import { approveAction, fmtDate, rejectAction, rupees } from '../services/api';
import { ACTION_LABEL } from '../views/common';
import type { ToastTone } from './Feedback';

interface Props {
  action: Action;
  canDecide: boolean;
  onDecided?: (action: Action) => void;
  onToast: (text: string, tone?: ToastTone) => void;
  onOpenApprovals?: () => void;
}

// A proposed action inside the chat or the timetable: review, edit, then approve or reject.
export function ActionPreview({ action: initial, canDecide, onDecided, onToast, onOpenApprovals }: Props) {
  const [action, setAction] = useState(initial);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [excluded, setExcluded] = useState<number[]>([]);
  const [method, setMethod] = useState('UPI');
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const items = action.payload.items ?? [];
  const pending = action.status === 'pending';
  const isEntry = ENTRY_TYPES.includes(action.action_type);          // typed-in data: confirm and save
  const included = items.length - excluded.length;

  async function approve() {
    setBusy(true);
    try {
      const e: Record<string, unknown> = {};
      if (action.action_type === 'send_whatsapp') {
        const changed: Record<string, { message: string }> = {};
        Object.entries(edits).forEach(([i, msg]) => { if (msg !== items[Number(i)]?.message) changed[i] = { message: msg }; });
        if (Object.keys(changed).length) e.items = changed;
        if (excluded.length) e.exclude = excluded;
      }
      if (action.action_type === 'mark_instalment_paid') e.method = method;
      const updated = await approveAction(action.id, e);
      setAction(updated);
      onDecided?.(updated);
      onToast(isEntry ? 'Saved · recorded in the audit log' :
        action.action_type === 'send_whatsapp' ? `${included} message(s) approved and logged` :
        action.action_type === 'mark_instalment_paid' ? 'Instalment marked paid' :
        action.action_type === 'issue_certificate' ? 'Certificates issued · recorded in the audit log' :
        action.action_type === 'request_leave' ? 'Leave approved · recorded in the audit log' : 'Timetable updated', 'success');
    } catch (err) {
      onToast((err as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    setBusy(true);
    try {
      const updated = await rejectAction(action.id, reason.trim());
      setAction(updated);
      setRejecting(false);
      onDecided?.(updated);
      onToast('Proposal rejected and recorded in the audit log', 'warning');
    } catch (err) {
      onToast((err as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  const toggle = (i: number) => setExcluded((x) => (x.includes(i) ? x.filter((n) => n !== i) : [...x, i]));

  return (
    <div className={`chat-action ${pending ? 'is-pending' : 'is-decided'}`}>
      <div className="chat-action-head">
        <span className="chat-action-icon">{action.action_type === 'send_whatsapp' ? <MessageCircle size={14} /> : <ShieldCheck size={14} />}</span>
        <div><small>{ACTION_LABEL[action.action_type]}</small><b>{action.title}</b></div>
        <span className={`status-pill ${pending ? 'status-review' : action.status === 'approved' ? 'status-ok' : 'status-bad'}`}><i />
          {pending ? (isEntry ? 'Not saved yet' : 'Waiting for approval') : action.status === 'approved' ? (isEntry ? 'Saved' : 'Approved') : action.status === 'rejected' ? (isEntry ? 'Discarded' : 'Rejected') : 'Failed'}</span>
      </div>

      {action.action_type === 'send_whatsapp' && (
        <div className="chat-msg-list">
          {items.map((it, i) => {
            const off = excluded.includes(i);
            return (
              <div key={i} className={`chat-msg ${off ? 'off' : ''}`}>
                <label className="chat-msg-to">
                  {pending && canDecide && <input type="checkbox" checked={!off} onChange={() => toggle(i)} />}
                  <b>{it.name}</b><span>{it.phone}</span>
                </label>
                {pending && canDecide
                  ? <textarea className="form-textarea" rows={3} value={edits[i] ?? it.message ?? ''} disabled={off}
                              onChange={(e) => setEdits({ ...edits, [i]: e.target.value })} aria-label={`Message to ${it.name}`} />
                  : <p>{it.message}</p>}
              </div>
            );
          })}
          {(action.payload.skipped_no_phone?.length ?? 0) > 0 && (
            <small className="chat-action-note">Skipped (no phone number): {action.payload.skipped_no_phone?.join(', ')}</small>
          )}
        </div>
      )}

      {action.action_type === 'mark_instalment_paid' && (
        <div className="chat-paid">
          {items.map((it) => (
            <div key={it.instalment_id} className="chat-paid-row">
              <b>{it.student_name}</b><span>Due {fmtDate(it.due_date)}</span><span className="mono-cell">{rupees(it.balance)}</span>
            </div>
          ))}
          {pending && canDecide && (
            <label className="chat-paid-method">Paid by
              <div className="select-field small-select">
                <select value={method} onChange={(e) => setMethod(e.target.value)}>
                  <option value="UPI">UPI</option><option value="bank_transfer">Bank transfer</option>
                  <option value="cash">Cash</option><option value="card">Card</option>
                </select><ChevronDown size={13} />
              </div>
            </label>
          )}
        </div>
      )}

      {action.action_type === 'apply_timetable' && <ChangeList proposal={action.payload as Proposal} />}
      {action.payload.preview && <EntryPreviewBody preview={action.payload.preview} />}

      {action.status === 'approved' && <div className="resolved-note"><Check size={12} /> {resultText(action)}</div>}
      {action.status === 'approved' && <WaSendList action={action} />}
      {action.status === 'approved' && <ResultLinks action={action} />}
      {action.status === 'rejected' && <div className="resolved-note"><XCircle size={12} /> {isEntry ? 'Discarded. Nothing was saved.' : 'Rejected. Nothing was sent or changed.'}</div>}
      {action.status === 'failed' && <div className="resolved-note"><XCircle size={12} /> {String(action.result?.error ?? 'Failed. Nothing was changed.')}</div>}

      {pending && canDecide && !rejecting && (
        <div className="approval-actions">
          <button className="approve-action" disabled={busy || (action.action_type === 'send_whatsapp' && included === 0)} onClick={() => void approve()}>
            <Check size={14} /> {busy ? 'Working…' : isEntry ? 'Confirm and save' : action.action_type === 'send_whatsapp' ? `Approve and send ${included}` :
              action.action_type === 'mark_instalment_paid' ? 'Approve and mark paid' :
              action.action_type === 'issue_certificate' ? `Approve and issue ${items.length}` :
              action.action_type === 'request_leave' ? 'Approve leave' : 'Approve and apply'}
          </button>
          <button className="reject-action" disabled={busy} onClick={() => setRejecting(true)}><XCircle size={14} /> {isEntry ? 'Discard' : 'Reject'}</button>
          {onOpenApprovals && <button className="text-button" onClick={onOpenApprovals}>Open in Approvals <ArrowRight size={12} /></button>}
        </div>
      )}
      {pending && canDecide && rejecting && (
        <div className="chat-reject">
          <textarea className="form-textarea" rows={2} autoFocus placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="approval-actions">
            <button className="reject-action filled-reject" disabled={busy} onClick={() => void reject()}><XCircle size={14} /> Reject proposal</button>
            <button className="text-button" onClick={() => setRejecting(false)}>Cancel</button>
          </div>
        </div>
      )}
      {pending && !canDecide && <span className="readonly-note"><ShieldCheck size={13} /> Waiting for someone with approval rights.</span>}
    </div>
  );
}

export function ChangeList({ proposal }: { proposal: Partial<Proposal> }) {
  const changes = proposal.changes ?? [];
  return (
    <div className="tt-change-list">
      {changes.length === 0 && <p className="chat-action-note">No classes need to move.</p>}
      {changes.map((c, i) => (
        <div key={i} className="tt-change">
          <div className="tt-change-line">
            <b>{c.batch_code}</b>
            <s>{c.from ?? 'new class'}</s>
            <ArrowRight size={12} />
            <span>{c.to ?? 'removed'}</span>
          </div>
          <small>{c.reason}</small>
        </div>
      ))}
      {(proposal.unscheduled?.length ?? 0) > 0 && (
        <div className="tt-unscheduled">Could not place: {proposal.unscheduled?.map((u) => `${u.batch_code} (${u.reason})`).join('; ')}</div>
      )}
      {proposal.elapsed_ms !== undefined && <small className="chat-action-note">Planned in {proposal.elapsed_ms} ms. Every other class stays where it is.</small>}
    </div>
  );
}

// Fields, rows, checks and warnings of a data-entry preview (new student, payment, attendance, marks)
export function EntryPreviewBody({ preview }: { preview: EntryPreview }) {
  return (
    <div className="entry-preview">
      <p className="entry-preview-summary">{preview.summary}</p>
      <dl className="entry-preview-fields">{preview.fields.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v || '–'}</dd></div>)}</dl>
      {preview.table && preview.table.rows.length > 0 && (
        <div className="data-table-wrap"><table className="data-table compact-table">
          <thead><tr>{preview.table.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{preview.table.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={String(v) === 'absent' ? 'att-absent' : String(v) === 'late' ? 'att-late' : ''}>{v}</td>)}</tr>)}</tbody>
        </table></div>
      )}
      {preview.checks.map((c) => <div key={c} className="entry-check"><Check size={12} /> {c}</div>)}
      {preview.warnings.map((w) => <div key={w} className="entry-warning"><CircleAlert size={12} /> {w}</div>)}
    </div>
  );
}

// Pages an approved action produced (certificates, receipts): each opens in a new tab, ready to print.
export function ResultLinks({ action }: { action: Action }) {
  const links = (action.result?.links as { label: string; url: string; kind?: string }[] | undefined) ?? [];
  if (action.status !== 'approved' || !links.length) return null;
  const Icon = action.action_type === 'issue_certificate' ? Award : FileText;
  return (
    <div className="result-links">
      {links.map((l) => <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className={`result-link ${l.kind === 'whatsapp' ? 'wa-send-button' : ''}`}>
        {l.kind === 'whatsapp' ? <MessageCircle size={14} /> : <Icon size={14} />} {l.label}</a>)}
    </div>
  );
}

type SentItem = { name: string; phone: string; status: string; wa_link?: string; wa_to?: string };

// Messages that were not sent automatically: one tap opens WhatsApp with the text typed, a person presses Send.
export function WaSendList({ action }: { action: Action }) {
  const [done, setDone] = useState<number[]>([]);
  if (action.action_type !== 'send_whatsapp' || action.status !== 'approved') return null;
  const sent = ((action.result?.sent as SentItem[] | undefined) ?? []).filter((s) => s.wa_link);
  if (!sent.length) return null;
  return (
    <div className="wa-send">
      <small>Send on WhatsApp: each button opens WhatsApp with the message typed, to the number shown. Press Send there.</small>
      <div className="wa-send-list">
        {sent.map((s, i) => (
          <a key={i} href={s.wa_link} target="_blank" rel="noreferrer" className={`wa-send-button ${done.includes(i) ? 'done' : ''}`}
             title={s.wa_to && s.wa_to !== s.phone ? `Demo: sample number, opens ${s.wa_to}` : `Opens ${s.wa_to ?? s.phone}`}
             onClick={() => setDone((d) => (d.includes(i) ? d : [...d, i]))}>
            {done.includes(i) ? <Check size={14} /> : <MessageCircle size={14} />} {s.name}
            <span className="wa-send-to">{s.wa_to && s.wa_to !== s.phone ? `demo → ${s.wa_to}` : s.wa_to ?? s.phone}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

export function resultText(action: Action) {
  const r = action.result ?? {};
  if (typeof r.message === 'string') return r.message;
  if (action.action_type === 'send_whatsapp') {
    const sent = (r.sent as SentItem[] | undefined) ?? [];
    const real = sent.filter((s) => s.status.startsWith('sent')).length;
    const failed = sent.filter((s) => s.status.startsWith('failed'));
    const ready = sent.filter((s) => s.status.startsWith('ready')).length;
    const simulated = sent.length - real - failed.length - ready;
    const parts: string[] = [];
    if (real) parts.push(`${real} sent on WhatsApp`);
    if (ready) parts.push(`${ready} ready to send: tap the green buttons`);
    if (simulated) parts.push(`${simulated} simulated (logged only)`);
    if (failed.length) parts.push(`${failed.length} failed: ${failed[0].status.replace(/^failed: /, '')}`);
    const to = (r.delivery as { redirect_to?: string } | undefined)?.redirect_to;
    return (parts.join(', ') || 'Nothing to send') + (real && to ? ` (demo mode: delivered to ${to})` : '') + '.';
  }
  if (action.action_type === 'mark_instalment_paid') {
    const n = (r.marked_paid as unknown[] | undefined)?.length ?? 0;
    return `${n} instalment(s) marked paid and a payment recorded.`;
  }
  return `Timetable updated: ${String(r.applied_slots ?? '')} classes this week.`;
}
