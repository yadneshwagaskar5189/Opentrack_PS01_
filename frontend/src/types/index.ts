// Shapes returned by the FastAPI backend (see the backend README, section 3).
export type Role = 'owner' | 'counselor' | 'accounts' | 'trainer';
export type ViewId = 'dashboard' | 'copilot' | 'approvals' | 'timetable' | 'risk' | 'students' | 'entry' | 'reconciliation' | 'audit';

export interface User {
  id: number;
  username: string;
  role: Role;
  display_name: string;
  trainer_id: number | null;
}

export interface Me {
  user: User;
  role_label: string;
  can_see_fees: boolean;
  allowed_actions: string[];
  restrictions: string;
  data_views: string[];
}

export interface Health {
  ok: boolean;
  llm: string;
  llm_key_set: boolean;
  today: string;
  institute?: string;
  plan_week?: [string, string];
  whatsapp?: { provider: string; real: boolean; note: string; redirect_to?: string };
}

export type Row = Record<string, string | number | null>;

export interface ActionItem {
  student_id?: number | null;
  name?: string;
  phone?: string;
  message?: string;
  instalment_id?: number;
  student_name?: string;
  due_date?: string;
  amount?: number;
  balance?: number;
}

export interface Slot {
  id?: number;
  batch_id: number;
  batch_code: string;
  course_name?: string;
  trainer_id: number;
  trainer_name: string;
  room_id: number | null;
  room_name: string;
  day_of_week: number;
  day_name: string;
  start_hour: number;
  end_hour: number;
  is_online: number;
  mode: string;
  has_problem?: boolean;
}

export interface TimetableChange {
  batch_code: string;
  type: string;
  from: string | null;
  to: string | null;
  reason: string;
}

export interface Proposal {
  week_start: string;
  strategy: string;
  slots: Slot[];
  changes: TimetableChange[];
  unscheduled: { batch_code: string; reason: string }[];
  problems_found: string[];
  violations_after: string[];
  elapsed_ms: number;
}

export type ActionType = 'send_whatsapp' | 'mark_instalment_paid' | 'apply_timetable'
  | 'add_student' | 'record_payment' | 'mark_attendance' | 'save_assignment' | 'issue_certificate' | 'request_leave';
export const ENTRY_TYPES: ActionType[] = ['add_student', 'record_payment', 'mark_attendance', 'save_assignment'];
export type ActionStatus = 'pending' | 'approved' | 'rejected' | 'failed';

export interface Action {
  id: number;
  created_at: string;
  created_by: number;
  created_by_name?: string;
  action_type: ActionType;
  title: string;
  status: ActionStatus;
  decided_at?: string | null;
  payload: Partial<Proposal> & { items?: ActionItem[]; skipped_no_phone?: string[]; method?: string; preview?: EntryPreview };
  result: Record<string, unknown> | null;
}

export type ResponseType = 'answer' | 'action_preview' | 'blocked' | 'refusal' | 'error';

export interface CopilotResponse {
  type: ResponseType;
  answer: string;
  sql: string | null;
  explanation?: string | null;
  columns: string[];
  rows: Row[];
  row_count: number;
  truncated: boolean;
  objects_read: string[];
  blocked: string[];
  action?: Action | null;
  proposal?: Proposal | null;
  audit_id?: number | null;
  llm?: string;
  ms?: number;
  follow_ups?: string[];          // natural next questions, already filtered for this role
}

export interface Timetable {
  week_start: string;
  week_end: string;
  days: string[];
  slots: Slot[];
  problems: string[];
  online_licences: number;
}

export interface PlannerResult {
  message: string;
  proposal: Proposal;
  action: Action | null;
}

export interface AuditEntry {
  id: number;
  ts: string;
  user_id: number | null;
  username: string | null;
  role: string | null;
  event_type: string;
  question: string | null;
  sql_text: string | null;
  outcome: string | null;
  detail: Record<string, unknown> | null;
  hash?: string | null;            // tamper seal (see backend app/audit.py)
}

export interface Trainer {
  id: number;
  name: string;
  skills: string;
}

export interface Money { count: number; amount: number }

export interface Dashboard {
  role: Role;
  today: string;
  active_students?: number;
  active_batches?: number;
  timetable_problems?: number;
  pending_actions: number;
  at_risk?: { high: number; medium: number };
  fees?: { due_this_week: Money; overdue: Money; collected_this_month: number };
  my_batches?: { code: string; course: string; mode: string; students: number }[];
  attendance_last_14_days_pct?: number | null;
  classes_today?: ClassToday[];
  collections_by_method?: { method: string; payments: number; amount: number }[];
}

export interface RiskStudent {
  student_id: number;
  name: string;
  batch_code: string;
  risk_score: number;
  level: 'high' | 'medium';
  reasons: string[];
  signals: Record<string, number | null>;
}

export interface RiskReport {
  as_of: string;
  students: RiskStudent[];
  fee_signals_included: boolean;
  method: string;
}

export interface ClassToday {
  start_hour: number;
  end_hour: number;
  batch_code: string;
  trainer_name: string;
  room_name: string | null;
  is_online: number;
  trainer_on_leave?: number;
}

export interface Course {
  id: number;
  code: string;
  name: string;
  duration_weeks: number;
}

export interface BatchRequest {
  id: number;
  code: string;
  course_name: string;
  course_code: string;
  expected_students: number;
  online_seats_needed: number;
  preferred_days: string[];
  preferred_start: string | null;
  preferred_end: string | null;
  session_hours: number;
  classroom_size: number;
  online_size: number;
  mode: string | null;
  note: string | null;
  already_added: boolean;
  trainers: { id: number; name: string }[];
}

export type ReconStatus = 'matched' | 'partial' | 'duplicate' | 'unmatched' | 'not_fee' | 'not_in_bank';

export interface ReconLine {
  id: number;
  txn_ref?: string | null;
  txn_date?: string;
  description?: string;
  credit?: number;
  utr?: string | null;
  payer?: string;
  status: ReconStatus;
  note: string;
  student_name: string | null;
  recorded_amount?: number | null;
  amount?: number;
  paid_on?: string;
  method?: string;
  reference?: string | null;
}

export interface Reconciliation {
  period: { from: string | null; to: string | null };
  summary: Partial<Record<ReconStatus, number>>;
  bank_total: number;
  lines: ReconLine[];
  not_in_bank: ReconLine[];
  needs_attention: number;
  method: string;
}

// ---- typing data in (forms and copilot)
export interface EntryPreview {
  summary: string;
  fields: [string, string][];
  table: { columns: string[]; rows: (string | number)[][] } | null;
  checks: string[];
  warnings: string[];
}

export interface EntryBatch {
  id: number;
  code: string;
  name: string;
  course_name: string;
  mode: string;
  status: string;
  days: string | null;
  start_time: string | null;
  end_time: string | null;
  start_date: string;
  end_date: string;
  online_seat_limit: number;
  online_students: number;
  classroom_students: number;
  trainer_name: string;
  room_name: string | null;
  room_capacity?: number | null;
}

export interface EntryOptions {
  forms: ActionType[];
  today: string;
  batches: EntryBatch[];
  courses?: { code: string; name: string; total_fee: number; emi_count: number }[];
  trainers?: { id: number; name: string }[];      // owner only: for leave requests
}

export interface RosterStudent { student_id: number; name: string; learning_mode: string; status?: string | null }

export interface Roster {
  batch: { id: number; code: string; name: string; days: string | null; start_time: string | null; end_time: string | null };
  date: string;
  usual_day: boolean | null;
  session_id: number | null;
  topic: string | null;
  students: RosterStudent[];
}

export interface AssignmentsInfo {
  batch: { id: number; code: string; name: string };
  assignments: { id: number; title: string; due_date: string; submitted: number }[];
  students: RosterStudent[];
  scores: Record<string, Record<string, { score: number | null; status: string }>>;
}

export interface OpenInstalment {
  instalment_id: number;
  student_name: string;
  batch_code: string;
  instalment_no: number;
  due_date: string;
  amount: number;
  paid_amount: number;
  balance: number;
  status: string;
}

// A checked everyday question the copilot knows well (GET /api/copilot/questions)
export interface CopilotQuestion { id: string; topic: string; question: string; explanation: string }

// GET /api/audit/verify: the audit log's tamper seals
export interface AuditVerify { ok: boolean; entries: number; problems: { id: number; problem: string }[]; message: string; last_entry: { id: number; ts: string } | null; checked_at?: string }

// GET /api/brief: the morning brief
export interface BriefSection { key: string; title: string; lines: string[]; tone: 'info' | 'warn' | 'ok'; ask: string | null }
export interface Brief { date: string; headline: string; sections: BriefSection[]; text: string }
