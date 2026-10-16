import { useCallback, useEffect, useState } from 'react';
import { MessageCircle, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { RiskReport } from '../types';
import * as api from '../services/api';
import type { ViewProps } from './common';

export function RiskView({ me, askCopilot }: ViewProps) {
  const [level, setLevel] = useState<'medium' | 'high'>('medium');
  const [data, setData] = useState<RiskReport | null>(null);
  const [error, setError] = useState('');
  const canMessage = me.user.role === 'owner' || me.user.role === 'counselor';

  const load = useCallback(() => {
    setError('');
    setData(null);
    api.getAtRisk(level).then(setData).catch((e: Error) => setError(e.message));
  }, [level]);
  useEffect(load, [load]);

  const high = data?.students.filter((s) => s.level === 'high').length ?? 0;

  return <section className="view-page risk-view">
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><TriangleAlert size={12} /></span> EARLY WARNING · WITH REASONS</div><h1>Students <span>at risk</span></h1><p>Attendance, assignments{data?.fee_signals_included ? ' and payments' : ''} combined into one score, with the reasons behind every flag. A flag starts a conversation; it isn't a verdict.</p></div>
      <div className="approval-heading-stat"><b>{String(high).padStart(2, '0')}</b><span>HIGH<br />RISK</span><span className="stat-pulse" /></div></div>
    <div className="approval-toolbar"><div className="filter-tabs" role="tablist" aria-label="Risk level">
      <button role="tab" aria-selected={level === 'medium'} className={level === 'medium' ? 'selected' : ''} onClick={() => setLevel('medium')}>High and medium</button>
      <button role="tab" aria-selected={level === 'high'} className={level === 'high' ? 'selected' : ''} onClick={() => setLevel('high')}>High only</button>
    </div></div>
    {data && <p className="method-note"><ShieldCheck size={13} /> {data.method}{!data.fee_signals_included && ' Fee signals are removed for your role.'}</p>}
    {error && <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div>}
    {!data && !error && <div className="loading-row"><span className="live-pip" /> Scoring students…</div>}
    {data && data.students.length === 0 && <div className="empty-state panel"><b>No students are flagged right now.</b></div>}
    <div className="risk-grid">
      {data?.students.map((s) => <article key={`${s.student_id}-${s.batch_code}`} className={`panel risk-card lvl-${s.level}`}>
        <div className="risk-head"><div><h3>{s.name}</h3><span className="mono-cell">{s.batch_code}</span></div>
          <div className="risk-score"><span className={`status-pill ${s.level === 'high' ? 'status-bad' : 'status-warning'}`}><i />{s.level}</span><div className="risk-meter"><div style={{ width: `${s.risk_score}%` }} /></div><b>{s.risk_score}</b></div></div>
        <ul className="risk-reasons">{s.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        {canMessage && <button className="outline-action" onClick={() => askCopilot(`Draft a friendly WhatsApp check-in message for ${s.name} in ${s.batch_code}`)}><MessageCircle size={13} /> Draft a check-in message</button>}
      </article>)}
    </div>
  </section>;
}
