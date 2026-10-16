import { useState } from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, ArrowUpRight, BookOpenCheck, BrainCircuit, Check, Fingerprint, ShieldCheck, Sparkles, Workflow } from 'lucide-react';
import type { Health, Me, Role } from '../types';
import { DEMO_ACCOUNTS, ROLE_ORDER } from '../services/api';

const cardColor: Record<Role, string> = { owner: '', counselor: 'role-emerald', accounts: 'role-amber', trainer: 'role-rose' };

export function LoginView({ onSignIn, health }: { onSignIn: (role: Role) => Promise<Me>; health: Health | null }) {
  const [busy, setBusy] = useState<Role | null>(null);
  const [error, setError] = useState('');

  async function choose(role: Role) {
    setBusy(role);
    setError('');
    try {
      await onSignIn(role);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  return (
    <div className="login-screen">
      <section className="login-story">
        <div className="login-brand"><span className="brand-emblem large"><BookOpenCheck size={22} /><i /></span><span>institute<span>.</span></span></div>
        <div className="story-orbit orbit-one" /><div className="story-orbit orbit-two" />
        <div className="story-content">
          <div className="eyebrow"><span className="eyebrow-mark"><Sparkles size={12} /></span> THE OPERATIONS COPILOT FOR TRAINING INSTITUTES</div>
          <h1>Complexity,<br /><span>in good hands.</span></h1>
          <p>Ask about students, fees, attendance and the timetable in plain language. Every answer shows its query, every role sees only its own data, and nothing changes until a person approves it.</p>
          <div className="story-feature-list">
            <div><BrainCircuit size={17} /><span><b>Natural language</b><small>Ask the institute. See the query.</small></span><Check size={14} /></div>
            <div><Workflow size={17} /><span><b>Human-led automation</b><small>Messages and changes wait for approval.</small></span><Check size={14} /></div>
            <div><Fingerprint size={17} /><span><b>Role-aware by design</b><small>The database itself enforces each role.</small></span><Check size={14} /></div>
          </div>
          <div className="story-quote"><span>“</span><div><p>A trainer must never see fee details, however the question is phrased.</p><small>INSTITUTE COPILOT · DESIGN RULE 01</small></div></div>
        </div>
        <div className="story-bottom"><span><ShieldCheck size={13} /> ACCOUNTABLE BY DEFAULT</span><span>{(health?.institute ?? 'Your institute').toUpperCase()} <ArrowUpRight size={12} /></span></div>
      </section>
      <section className="login-panel">
        <div className="login-topline">
          <span>DEMO WORKSPACE</span>
          <span className="login-status"><i /> {health ? (health.llm === 'mock' ? 'OFFLINE DEMO MODE' : 'SYSTEM READY') : 'CONNECTING…'}</span>
        </div>
        <div className="login-panel-inner">
          <div className="entry-icon"><BookOpenCheck size={20} /></div>
          <div className="eyebrow muted-eyebrow">CHOOSE AN ACCOUNT</div>
          <h2>Step inside.</h2>
          <p className="entry-description">Each demo account signs in for real and gets that role's permissions, enforced by the database.</p>
          <div className="role-cards">
            {ROLE_ORDER.map((role, index) => {
              const acc = DEMO_ACCOUNTS[role];
              return (
                <motion.button key={role} className={`login-role-card ${cardColor[role]}`} initial={{ opacity: 0, y: 8 }}
                               animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * index }} whileHover={{ y: -2 }}
                               disabled={busy !== null} onClick={() => void choose(role)}>
                  <span className="login-role-glyph">{acc.initials}</span>
                  <span className="login-role-copy"><b>{acc.name}</b><small>{acc.label} · {acc.subtitle}</small></span>
                  <ArrowRight size={15} className="login-arrow" />
                  <span className="role-scope-hint">{busy === role ? 'Signing in…' : acc.scope}</span>
                </motion.button>
              );
            })}
          </div>
          {error && <div className="login-error" role="alert">{error}</div>}
          <div className="demo-note"><ShieldCheck size={14} /><span>Live institute database · the copilot is read-only and every change needs approval</span></div>
          <div className="role-view-note"><span>Judge mode</span><span>Switch accounts any time from the header</span></div>
        </div>
        <div className="login-panel-foot"><span>INSTITUTE COPILOT <span>·</span> V1.0</span><span>PRIVACY &amp; SECURITY BUILT IN</span></div>
      </section>
    </div>
  );
}
