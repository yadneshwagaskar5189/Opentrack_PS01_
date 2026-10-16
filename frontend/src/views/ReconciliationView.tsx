import { useCallback, useEffect, useMemo, useState } from 'react';
import { Landmark, LockKeyhole, RefreshCw, ShieldCheck } from 'lucide-react';
import type { ReconLine, ReconStatus, Reconciliation } from '../types';
import * as api from '../services/api';
import type { ViewProps } from './common';

const LABEL: Record<ReconStatus, string> = {
  matched: 'Matched', partial: 'Short / over paid', duplicate: 'Duplicate credit', unmatched: 'Not recorded',
  not_fee: 'Not a fee', not_in_bank: 'Missing from bank',
};
const PILL: Record<ReconStatus, string> = {
  matched: 'status-ok', partial: 'status-warning', duplicate: 'status-bad', unmatched: 'status-bad',
  not_fee: 'status-review', not_in_bank: 'status-warning',
};
const ORDER: ReconStatus[] = ['unmatched', 'partial', 'duplicate', 'not_in_bank', 'not_fee', 'matched'];

export function ReconciliationView({ askCopilot }: ViewProps) {
  const [data, setData] = useState<Reconciliation | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<'attention' | 'all' | ReconStatus>('attention');

  const load = useCallback(() => {
    setError('');
    api.getReconciliation().then(setData).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const rows: ReconLine[] = useMemo(() => {
    if (!data) return [];
    const all = [...data.lines, ...data.not_in_bank];
    const attention = (r: ReconLine) => r.status !== 'matched' || r.note.startsWith('Matched on UTR');
    const picked = all.filter((r) => filter === 'all' || (filter === 'attention' ? attention(r) : r.status === filter));
    return picked.sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || String(a.txn_date ?? a.paid_on).localeCompare(String(b.txn_date ?? b.paid_on)));
  }, [data, filter]);

  return <section className="view-page recon-view">
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><Landmark size={12} /></span> BANK STATEMENT VS RECORDED PAYMENTS</div><h1>Fee <span>reconciliation</span></h1>
      <p>Every bank credit is matched to a recorded payment by its UTR. Anything that doesn't line up is listed with the reason, so accounts can fix it before month-end.</p></div>
      {data && <div className="approval-heading-stat"><b>{String(data.needs_attention).padStart(2, '0')}</b><span>NEED<br />ATTENTION</span><span className="stat-pulse" /></div>}
    </div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div>}
    {!data && !error && <div className="loading-row"><span className="live-pip" /> Matching bank lines…</div>}
    {data && <>
      <div className="approval-toolbar"><div className="filter-tabs" role="tablist" aria-label="Filter">
        <button role="tab" aria-selected={filter === 'attention'} className={filter === 'attention' ? 'selected' : ''} onClick={() => setFilter('attention')}>Needs attention <b>{data.needs_attention}</b></button>
        {ORDER.filter((k) => data.summary[k]).map((k) => <button key={k} role="tab" aria-selected={filter === k} className={filter === k ? 'selected' : ''} onClick={() => setFilter(k)}>{LABEL[k]} <b>{data.summary[k]}</b></button>)}
        <button role="tab" aria-selected={filter === 'all'} className={filter === 'all' ? 'selected' : ''} onClick={() => setFilter('all')}>All</button>
      </div><button className="outline-action" onClick={load}><RefreshCw size={13} /> Re-run</button></div>
      <p className="method-note"><ShieldCheck size={13} /> {data.method} Statement {api.fmtDate(data.period.from)} to {api.fmtDate(data.period.to)}, {api.rupees(data.bank_total)} credited. Read only: nothing is changed here.</p>
      <div className="panel results-panel"><div className="data-table-wrap"><table className="data-table recon-table">
        <thead><tr><th>Status</th><th>Date</th><th>Bank line / payment</th><th>Amount</th><th>Student</th><th>What it means</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={`${r.status}-${r.id}`}>
          <td><span className={`status-pill ${PILL[r.status]}`}><i />{LABEL[r.status]}</span></td>
          <td className="mono-cell">{api.fmtDate(r.txn_date ?? r.paid_on)}</td>
          <td><span className="mono-cell">{r.description ?? `${(r.method ?? '').replace('_', ' ')} · ${r.reference ?? ''}`}</span></td>
          <td>{api.rupees(r.credit ?? r.amount)}{r.recorded_amount != null && r.recorded_amount !== r.credit && <small className="muted-cell"> (recorded {api.rupees(r.recorded_amount)})</small>}</td>
          <td>{r.student_name ?? '–'}</td>
          <td className="recon-note">{r.note}</td>
        </tr>)}</tbody>
      </table></div>
        <div className="table-foot"><span>{rows.length} line(s)</span>
          <button className="text-button" onClick={() => askCopilot("Which bank credits don't match a payment?")}>Ask the copilot</button>
          <span className="read-only-pill"><LockKeyhole size={11} /> OWNER AND ACCOUNTS ONLY</span></div>
      </div>
    </>}
  </section>;
}
