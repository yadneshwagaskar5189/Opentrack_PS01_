import { Fragment, useCallback, useEffect, useState } from 'react';
import { AppShell, allowedViews } from './components/AppShell';
import { Toast, type ToastTone } from './components/Feedback';
import { LoginView } from './views/LoginView';
import { DashboardView } from './views/DashboardView';
import { CopilotView, type ChatMessage } from './views/CopilotView';
import { ApprovalsView } from './views/ApprovalsView';
import { TimetableView } from './views/TimetableView';
import { RiskView } from './views/RiskView';
import { StudentsView } from './views/StudentsView';
import { AuditLogView } from './views/AuditLogView';
import { ReconciliationView } from './views/ReconciliationView';
import { DataEntryView } from './views/DataEntryView';
import * as api from './services/api';
import type { Health, Me, Role, ViewId } from './types';

function viewFromHash(): ViewId | null {
  const v = window.location.hash.replace(/^#\/?/, '');
  return (['dashboard', 'copilot', 'approvals', 'timetable', 'risk', 'students', 'entry', 'reconciliation', 'audit'] as ViewId[]).includes(v as ViewId)
    ? (v as ViewId) : null;
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [booting, setBooting] = useState(Boolean(api.getToken()));
  const [health, setHealth] = useState<Health | null>(null);
  const [currentView, setCurrentView] = useState<ViewId>(viewFromHash() ?? 'dashboard');
  const [toast, setToast] = useState<{ text: string; tone: ToastTone } | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [queued, setQueued] = useState<{ text: string; id: number } | null>(null);

  const showToast = useCallback((text: string, tone: ToastTone = 'success') => {
    setToast({ text, tone });
    window.setTimeout(() => setToast((current) => (current?.text === text ? null : current)), 3600);
  }, []);

  const role: Role | null = me?.user.role ?? null;

  const navigate = useCallback((view: ViewId) => {
    setCurrentView(view);
    window.location.hash = `/${view}`;
  }, []);

  const refreshPending = useCallback(() => {
    if (!role || role === 'trainer') { setPendingCount(0); return; }
    api.listActions('pending').then((items) => setPendingCount(items.length)).catch(() => undefined);
  }, [role]);

  useEffect(() => {
    api.setUnauthorizedHandler(() => { setMe(null); setMessages([]); });
    api.getHealth().then(setHealth).catch(() => undefined);
    if (api.getToken()) {
      api.getMe().then(setMe).catch(() => setMe(null)).finally(() => setBooting(false));
    }
  }, []);

  useEffect(() => { refreshPending(); }, [refreshPending, currentView]);

  useEffect(() => {
    const onHash = () => { const v = viewFromHash(); if (v) setCurrentView(v); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  async function signIn(nextRole: Role) {
    await api.login(nextRole);
    api.getHealth().then(setHealth).catch(() => undefined);
    const profile = await api.getMe();
    setMessages([]);
    setMe(profile);
    const views = allowedViews(nextRole);
    if (!views.includes(currentView)) navigate(views[0]);
    return profile;
  }

  async function switchRole(nextRole: Role) {
    try {
      await api.logout();
      await signIn(nextRole);
      showToast(`Signed in as ${api.DEMO_ACCOUNTS[nextRole].name} (${api.DEMO_ACCOUNTS[nextRole].label})`, 'info');
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  }

  async function signOut() {
    await api.logout();
    setMe(null);
    setMessages([]);
  }

  // Other pages can hand a question to the copilot (dashboard tiles, at-risk cards).
  function askCopilot(text: string) {
    setQueued({ text, id: Date.now() });
    navigate('copilot');
  }

  const toastEl = <Toast message={toast?.text ?? ''} tone={toast?.tone} onClose={() => setToast(null)} />;

  if (booting) return <div className="boot-screen"><span className="live-pip" /> Connecting to Institute Copilot…</div>;
  if (!me || !role) return <><LoginView onSignIn={signIn} health={health} />{toastEl}</>;

  const views = allowedViews(role);
  const view = views.includes(currentView) ? currentView : views[0];
  const common = { me, onToast: showToast, onNavigate: navigate, refreshPending, askCopilot };

  return <div>
    <AppShell me={me} health={health} currentView={view} approvalsCount={pendingCount} onRoleChange={switchRole}
              onNavigate={navigate} onSignOut={signOut}>
      {/* keyed by user: switching role reloads the page's data for the new role */}
      <Fragment key={me.user.id}>
      {view === 'dashboard' && <DashboardView {...common} institute={health?.institute} />}
      {view === 'copilot' && <CopilotView {...common} health={health} messages={messages} setMessages={setMessages}
                                          queued={queued} clearQueued={() => setQueued(null)} />}
      {view === 'approvals' && <ApprovalsView {...common} />}
      {view === 'timetable' && <TimetableView {...common} health={health} />}
      {view === 'risk' && <RiskView {...common} />}
      {view === 'students' && <StudentsView {...common} />}
      {view === 'entry' && <DataEntryView {...common} />}
      {view === 'reconciliation' && <ReconciliationView {...common} />}
      {view === 'audit' && <AuditLogView {...common} />}
      </Fragment>
    </AppShell>
    {toastEl}
  </div>;
}
