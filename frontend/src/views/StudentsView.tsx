import { useCallback, useEffect, useMemo, useState } from 'react';
import { LockKeyhole, Search, UsersRound } from 'lucide-react';
import type { Row } from '../types';
import * as api from '../services/api';
import type { ViewProps } from './common';

export function StudentsView({ me }: ViewProps) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');

  const load = useCallback(() => {
    setError('');
    api.getStudents().then((d) => setRows(d.items)).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rows ?? []).filter((r) => !needle || Object.values(r).some((v) => String(v ?? '').toLowerCase().includes(needle)));
  }, [rows, q]);
  const columns = rows && rows.length ? Object.keys(rows[0]) : [];

  return <section className="view-page students-view">
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-mark"><UsersRound size={12} /></span> PEOPLE · FILTERED BY YOUR ROLE</div><h1>All <span>students</span></h1><p>{me.user.role === 'trainer' ? 'Students in your batches. Phone numbers and fees are never shown to trainers.' : 'Everyone enrolled, with their active batches.'}</p></div></div>
    <div className="audit-controls"><label className="audit-search"><Search size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, batch, city…" aria-label="Search students" /></label></div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-button" onClick={load}>Try again</button></div>}
    {!rows && !error && <div className="loading-row"><span className="live-pip" /> Loading students…</div>}
    {rows && <div className="panel results-panel">
      <div className="data-table-wrap"><table className="data-table"><thead><tr>{columns.map((c) => <th key={c}><button>{api.prettyCol(c)}</button></th>)}</tr></thead>
        <tbody>{filtered.map((r, i) => <tr key={i}>{columns.map((c) => <td key={c}>{/(_code|phone|batches)/.test(c) ? <span className="mono-cell">{api.formatCell(c, r[c])}</span> : api.formatCell(c, r[c])}</td>)}</tr>)}</tbody></table></div>
      <div className="table-foot"><span>{filtered.length} of {rows.length} students</span><span className="read-only-pill"><LockKeyhole size={11} /> READ ONLY</span></div>
    </div>}
  </section>;
}
