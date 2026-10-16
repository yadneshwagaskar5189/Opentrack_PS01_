import type { ToastTone } from '../components/Feedback';
import type { Me, ViewId } from '../types';

// Props every page receives from App.
export interface ViewProps {
  me: Me;
  onToast: (text: string, tone?: ToastTone) => void;
  onNavigate: (view: ViewId) => void;
  refreshPending: () => void;
  askCopilot: (question: string) => void;
}

export const ACTION_LABEL: Record<string, string> = {
  send_whatsapp: 'WhatsApp messages',
  mark_instalment_paid: 'Fee update',
  apply_timetable: 'Schedule change',
  add_student: 'New student',
  record_payment: 'Payment',
  mark_attendance: 'Attendance',
  save_assignment: 'Assignment & marks',
};
