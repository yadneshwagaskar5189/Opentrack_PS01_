import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, Check, CircleX, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

export type ToastTone = 'success' | 'warning' | 'error' | 'info';

export function Toast({ message, tone = 'success', onClose }: { message: string; tone?: ToastTone; onClose: () => void }) {
  const Icon = tone === 'success' ? Check : tone === 'error' ? CircleX : AlertTriangle;
  return <AnimatePresence>{message && <motion.div className={`toast toast-${tone}`} role="status" initial={{ opacity: 0, y: -10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8 }}><span className="toast-icon"><Icon size={15} /></span><span>{message}</span><button onClick={onClose} aria-label="Dismiss"><X size={14} /></button></motion.div>}</AnimatePresence>;
}

export function Modal({ title, eyebrow, children, onClose, size = 'normal' }: { title: string; eyebrow?: string; children: ReactNode; onClose: () => void; size?: 'normal' | 'wide' }) {
  // Rendered into <body>: an animated page (CSS transform) would otherwise trap position:fixed and cut the dialog off.
  return createPortal(<AnimatePresence><motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
    <motion.section className={`modal-card ${size === 'wide' ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} initial={{ opacity: 0, y: 12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: 0.98 }} transition={{ duration: 0.18 }}>
      <header className="modal-header"><div>{eyebrow && <small>{eyebrow}</small>}<h3>{title}</h3></div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={16} /></button></header>
      <div className="modal-content">{children}</div>
    </motion.section>
  </motion.div></AnimatePresence>, document.body);
}
