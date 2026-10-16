// Every call to the FastAPI backend lives here. The Vite dev server forwards /api to it.
import type {
  Action, AssignmentsInfo, AuditEntry, AuditVerify, Brief, BatchRequest, CopilotQuestion, CopilotResponse, Course, Dashboard, EntryOptions, Health, Me,
  OpenInstalment, PlannerResult, Reconciliation, RiskReport, Role, Roster, Row, Timetable, Trainer, User,
} from '../types';

const BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api';
const TOKEN_KEY = 'ic_token';

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage blocked: the session still works until reload */ }
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function request<T>(path: string, method: 'GET' | 'POST' = 'GET', body?: unknown): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the backend. Check that uvicorn is running on port 8000.", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== '/login') {
    setToken(null);
    onUnauthorized();
    throw new ApiError('Your session ended. Sign in again.', 401);
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown }).detail;
    throw new ApiError(typeof detail === 'string' ? detail : `Request failed (${res.status})`, res.status);
  }
  return data as T;
}

// ---------------------------------------------------------------- demo accounts
// One login per role, from seed_data/users.csv (all use the demo password).
export const DEMO_ACCOUNTS: Record<Role, {
  username: string; password: string; name: string; initials: string; label: string; subtitle: string; scope: string;
}> = {
  owner: { username: 'owner@brightpath.example', password: 'demo123', name: 'Kunal Phadke', initials: 'KP', label: 'Owner',
           subtitle: 'Runs the institute', scope: 'Full institute access' },
  counselor: { username: 'pooja@brightpath.example', password: 'demo123', name: 'Pooja Kulkarni', initials: 'PK', label: 'Counselor',
               subtitle: 'Student success', scope: 'Students, attendance, EMIs' },
  accounts: { username: 'accounts@brightpath.example', password: 'demo123', name: 'Savita Deshpande', initials: 'SD', label: 'Accounts',
              subtitle: 'Fees and payments', scope: 'Fees and payments only' },
  trainer: { username: 'rahul@brightpath.example', password: 'demo123', name: 'Rahul Deshmukh', initials: 'RD', label: 'Trainer',
             subtitle: 'Java & Python trainer', scope: 'Own batches, never fees' },
};
export const ROLE_ORDER: Role[] = ['owner', 'counselor', 'accounts', 'trainer'];

// ---------------------------------------------------------------- calls
export async function login(role: Role) {
  const acc = DEMO_ACCOUNTS[role];
  const res = await request<{ token: string; user: User }>('/login', 'POST', { username: acc.username, password: acc.password });
  setToken(res.token);
  return res.user;
}
export async function logout() {
  await request('/logout', 'POST').catch(() => undefined);
  setToken(null);
}
export const getMe = () => request<Me>('/me');
export const getHealth = () => request<Health>('/health');
export const getDashboard = () => request<Dashboard>('/dashboard');
export const getBrief = () => request<Brief>('/brief');

export const ask = (question: string, history: { question: string; sql: string }[]) =>
  request<CopilotResponse>('/copilot/ask', 'POST', { question, history });

// The checked everyday questions this role can ask (fee structure, today's lectures, which trainer...)
export const getQuestions = () => request<{ items: CopilotQuestion[] }>('/copilot/questions').then((d) => d.items);

// Runs raw SQL through the database guard with no AI involved: proves security doesn't depend on the AI.
export const trySql = (sql: string) => request<CopilotResponse>('/security/try-sql', 'POST', { sql });

export const listActions = (status?: string) =>
  request<{ items: Action[] }>(`/actions${status ? `?status=${status}` : ''}`).then((d) => d.items);
export const approveAction = (id: number, edits: Record<string, unknown> = {}) =>
  request<Action>(`/actions/${id}/approve`, 'POST', { edits });
export const rejectAction = (id: number, reason: string) =>
  request<Action>(`/actions/${id}/reject`, 'POST', { reason });

export const getTimetable = () => request<Timetable>('/timetable');
export const fixClashes = () => request<PlannerResult>('/timetable/fix-clashes', 'POST');
export const trainerUnavailable = (trainer_id: number, days: string[], reason: string) =>
  request<PlannerResult>('/timetable/trainer-unavailable', 'POST', { trainer_id, days, reason });
export const addBatch = (spec: Record<string, unknown>) => request<PlannerResult>('/timetable/add-batch', 'POST', spec);
export const getTrainers = () => request<{ items: Trainer[] }>('/trainers').then((d) => d.items);
export const getCourses = () => request<{ items: Course[] }>('/courses').then((d) => d.items);
export const getBatchRequests = () => request<{ items: BatchRequest[] }>('/timetable/requests').then((d) => d.items);
export const getReconciliation = () => request<Reconciliation>('/fees/reconciliation');

// Typing data in. Each POST only makes a preview (a pending action); approveAction() saves it.
export const entryOptions = () => request<EntryOptions>('/entry/options');
export const entryRoster = (batch: string, date: string) =>
  request<Roster>(`/entry/roster?batch=${encodeURIComponent(batch)}&date=${encodeURIComponent(date)}`);
export const entryAssignments = (batch: string) => request<AssignmentsInfo>(`/entry/assignments?batch=${encodeURIComponent(batch)}`);
export const entryInstalments = (q: string) =>
  request<{ items: OpenInstalment[] }>(`/entry/instalments?q=${encodeURIComponent(q)}`).then((d) => d.items);
type Proposed = { action: Action };
export const proposeStudent = (spec: Record<string, unknown>) => request<Proposed>('/entry/student', 'POST', spec).then((d) => d.action);
export const proposePayment = (spec: Record<string, unknown>) => request<Proposed>('/entry/payment', 'POST', spec).then((d) => d.action);
export const proposeAttendance = (spec: Record<string, unknown>) => request<Proposed>('/entry/attendance', 'POST', spec).then((d) => d.action);
export const proposeAssignment = (spec: Record<string, unknown>) => request<Proposed>('/entry/assignment', 'POST', spec).then((d) => d.action);
export const proposeLeave = (spec: Record<string, unknown>) => request<Proposed>('/entry/leave', 'POST', spec).then((d) => d.action);

export const verifyAudit = () => request<AuditVerify>('/audit/verify');
export const getAudit = (limit = 200) => request<{ items: AuditEntry[] }>(`/audit?limit=${limit}`).then((d) => d.items);
export const getAtRisk = (level: 'medium' | 'high' = 'medium') => request<RiskReport>(`/students/at-risk?level=${level}`);
export const getStudents = () => request<{ items: Row[]; count: number }>('/students');
export const resetDemo = () => request<{ ok: boolean }>('/admin/reset-demo', 'POST');

// ---------------------------------------------------------------- formatting
export const rupees = (n: number | null | undefined) => '₹' + Number(n || 0).toLocaleString('en-IN');
// 9 -> "09:00", 19.5 -> "19:30"
export function hh(h: number) {
  const mins = Math.round(Number(h) * 60);
  return `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}`;
}

// A trainer can teach a course if a skill equals the course code or is a word in the course name
// (same rule as the backend's timetable.teaches).
const escapeRegex = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export function teaches(skills: string, course: { code: string; name: string } | undefined) {
  if (!course) return false;
  const name = course.name.toLowerCase();
  return skills.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean).some((s) =>
    s === course.code.toLowerCase() || new RegExp('(^|[^a-z0-9])' + escapeRegex(s) + '($|[^a-z0-9])').test(name));
}

export function fmtDate(iso?: string | null) {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}
export function fmtTime(iso?: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
export function fmtDay(iso?: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }).toUpperCase();
}
export const timeNow = () => new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

const MONEY = /(amount|balance|fee|collected|revenue|pending|overdue|per_emi|credit)/i;
export function formatCell(col: string, value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '–';
  if (typeof value === 'number' && MONEY.test(col) && !/count|_no$|instalments/i.test(col)) return rupees(value);
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return fmtDate(value);
  return String(value);
}
export const prettyCol = (c: string) => c.replace(/_/g, ' ').replace(/^\w/, (m) => m.toUpperCase());
