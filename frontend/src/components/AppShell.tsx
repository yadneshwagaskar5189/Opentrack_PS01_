import { useState, type ReactNode } from 'react';
import { Activity, BookOpenCheck, CalendarDays, ChevronDown, ClipboardCheck, Command, GraduationCap, History, Landmark, LayoutDashboard, LogOut, PenLine, Search, ShieldCheck, Sparkles, TriangleAlert, Users, UsersRound, Bell } from 'lucide-react';
import type { Health, Me, Role, ViewId } from '../types';
import { DEMO_ACCOUNTS, ROLE_ORDER } from '../services/api';
import { canGrow, canShrink, getStep, setStep, zoomFor } from '../textSize';

const navItems: { id: ViewId; label: string; icon: typeof LayoutDashboard; meta: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, meta: '01' },
  { id: 'copilot', label: 'Copilot Studio', icon: Sparkles, meta: '02' },
  { id: 'approvals', label: 'Approvals', icon: ClipboardCheck, meta: '03' },
  { id: 'timetable', label: 'Timetable', icon: CalendarDays, meta: '04' },
  { id: 'risk', label: 'Students at risk', icon: TriangleAlert, meta: '05' },
  { id: 'students', label: 'Students', icon: UsersRound, meta: '06' },
  { id: 'entry', label: 'Add data', icon: PenLine, meta: '07' },
  { id: 'reconciliation', label: 'Fee reconciliation', icon: Landmark, meta: '08' },
  { id: 'audit', label: 'Audit log', icon: History, meta: '09' },
];

// Which pages each role sees. The backend enforces the same rules on every request.
const accessible: Record<Role, ViewId[]> = {
  owner: ['dashboard', 'copilot', 'approvals', 'timetable', 'risk', 'students', 'entry', 'reconciliation', 'audit'],
  counselor: ['dashboard', 'copilot', 'approvals', 'timetable', 'risk', 'students', 'entry', 'audit'],
  accounts: ['dashboard', 'copilot', 'approvals', 'students', 'entry', 'reconciliation', 'audit'],
  trainer: ['dashboard', 'copilot', 'timetable', 'risk', 'students', 'entry', 'audit'],
};

export function allowedViews(role: Role) {
  return accessible[role];
}

interface AppShellProps {
  me: Me;
  health: Health | null;
  currentView: ViewId;
  approvalsCount: number;
  onRoleChange: (role: Role) => void;
  onNavigate: (view: ViewId) => void;
  onSignOut: () => void;
  children: ReactNode;
}

export function AppShell({ me, health, currentView, approvalsCount, onRoleChange, onNavigate, onSignOut, children }: AppShellProps) {
  const role = me.user.role;
  const account = DEMO_ACCOUNTS[role];
  const initials = me.user.display_name.split(' ').map((p) => p[0]).join('').slice(0, 2);
  const live = health && health.llm !== 'mock';
  const items = navItems.filter((item) => accessible[role].includes(item.id));
  const [step, setStepState] = useState(getStep);
  const resize = (next: number) => { setStep(next); setStepState(next); };
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <button className="brand-lockup" onClick={() => onNavigate('dashboard')} aria-label="Institute Copilot home">
          <span className="brand-emblem"><BookOpenCheck size={19} strokeWidth={2.2} /><i /></span>
          <span className="brand-copy"><strong>institute<span>.</span></strong><small>COPILOT / GOVERNANCE</small></span>
        </button>
        <div className="workspace-chip"><span className="workspace-dot" /> {(health?.institute ?? 'Your institute').toUpperCase()}</div>
        <div className="nav-label">WORKSPACE <span>{items.length}</span></div>
        <nav className="primary-nav" aria-label="Main navigation">
          {items.map(({ id, label, icon: Icon, meta }) => (
            <button key={id} className={`nav-item ${currentView === id ? 'active' : ''}`} onClick={() => onNavigate(id)}>
              <Icon size={17} strokeWidth={1.8} /><span>{label}</span>
              {id === 'approvals' && approvalsCount > 0 ? <b className="nav-count">{approvalsCount}</b> : <small>{meta}</small>}
              {currentView === id && <i className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="compliance-card">
          <div className="compliance-icon"><ShieldCheck size={15} /></div>
          <div><b>Governance mode</b><span>Human approval required</span></div>
          <span className="live-pip" />
          <div className="compliance-foot"><span>DATABASE GUARD</span><span>ACTIVE <Activity size={10} /></span></div>
        </div>
        <div className="sidebar-footer">
          <div className="help-avatar"><GraduationCap size={17} /></div>
          <div className="help-copy"><b>{account.label} view</b><span>{account.scope}</span></div>
        </div>
        <button className="sidebar-user" onClick={onSignOut} title="Sign out">
          <span className="avatar">{initials}</span>
          <span className="user-info"><b>{me.user.display_name}</b><small>{account.label} · Signed in</small></span>
          <LogOut size={15} className="muted" />
        </button>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <div className="breadcrumbs"><span>{health?.institute ?? 'Institute'}</span><span className="slash">/</span><b>{navItems.find((n) => n.id === currentView)?.label}</b></div>
          <div className="topbar-tools">
            <span className="env-pill"><span className="live-pip" /> {live ? 'LIVE DATA' : health ? 'OFFLINE DEMO MODE' : 'CONNECTING'}</span>
            <div className="text-size" role="group" aria-label="Text size">
              <button onClick={() => resize(step - 1)} disabled={!canShrink(step)} aria-label="Smaller text" title="Smaller text">A−</button>
              <span>{Math.round(zoomFor(step) * 100)}%</span>
              <button onClick={() => resize(step + 1)} disabled={!canGrow(step)} aria-label="Larger text" title="Larger text">A+</button>
            </div>
            <button className="icon-button search-trigger" aria-label="Ask the copilot" onClick={() => onNavigate('copilot')}>
              <Search size={16} /><span>Ask anything</span><kbd>AI</kbd>
            </button>
            {role !== 'trainer' && (
              <button className="icon-button bell-button" aria-label="Approvals waiting" onClick={() => onNavigate('approvals')}>
                <Bell size={16} />{approvalsCount > 0 && <i />}
              </button>
            )}
            <div className="topbar-divider" />
            <div className="role-switcher-wrap">
              <label htmlFor="role-select" className="role-kicker"><Users size={12} /> VIEW AS</label>
              <div className="role-select-shell"><span className="role-avatar">{account.initials}</span>
                <select id="role-select" value={role} onChange={(e) => onRoleChange(e.target.value as Role)} aria-label="Sign in as another demo role">
                  {ROLE_ORDER.map((r) => <option key={r} value={r}>{DEMO_ACCOUNTS[r].label}: {DEMO_ACCOUNTS[r].name}</option>)}
                </select><ChevronDown size={13} /></div>
              <span className="role-scope"><span className="scope-dot" />{account.scope}</span>
            </div>
          </div>
        </header>
        <div className="content-scroll">{children}</div>
        <footer className="app-footer">
          <span><Command size={12} /> INSTITUTE COPILOT</span>
          <span><span className="live-pip" /> {live ? `LIVE DATABASE · AI: ${health?.llm.toUpperCase()}` : 'OFFLINE DEMO MODE · SAMPLE ANSWERS'}</span>
          <span>READ-ONLY COPILOT <span className="footer-sep">/</span> EVERY CHANGE APPROVED</span>
        </footer>
      </main>
    </div>
  );
}
