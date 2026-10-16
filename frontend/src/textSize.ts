// Text size. The design uses a compact type scale, so the whole page is scaled with CSS zoom:
// bigger automatically on wide screens (about 137% on a 1920px screen), and the A- / A+ buttons
// in the top bar adjust it (remembered in this browser). The zoom never makes the layout narrower
// than 1200px, so nothing gets squashed; phones are left at 100%.
const KEY = 'ic_text_size';
const MIN_STEP = -2;
const MAX_STEP = 3;

const base = () => Math.min(Math.max(window.innerWidth / 1400, 1), 1.4);
const maxZoom = () => Math.max(window.innerWidth / 1200, 1);

export function getStep(): number {
  try {
    const n = Number(localStorage.getItem(KEY) ?? 0);
    return Number.isFinite(n) ? Math.min(Math.max(Math.round(n), MIN_STEP), MAX_STEP) : 0;
  } catch { return 0; }
}

export function zoomFor(step: number): number {
  return Number(Math.min(Math.max(base() * (1 + 0.12 * step), 0.8), maxZoom()).toFixed(3));
}

export function applyTextSize(step = getStep()): number {
  const z = zoomFor(step);
  const root = document.documentElement;
  root.style.setProperty('zoom', String(z));
  root.style.setProperty('--ui-zoom', String(z));
  return z;
}

export function setStep(step: number): number {
  const s = Math.min(Math.max(step, MIN_STEP), MAX_STEP);
  try { localStorage.setItem(KEY, String(s)); } catch { /* storage blocked: still applies until reload */ }
  return applyTextSize(s);
}

export const canGrow = (step: number) => step < MAX_STEP && zoomFor(step + 1) > zoomFor(step);
export const canShrink = (step: number) => step > MIN_STEP && zoomFor(step - 1) < zoomFor(step);
