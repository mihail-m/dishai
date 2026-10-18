import type { ReactNode } from "react";

const svg = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;
export const Icon = {
  map: <svg {...svg}><path d="M9 4 3 6.5v13L9 17l6 3 6-2.5v-13L15 7 9 4Z" /><path d="M9 4v13M15 7v13" /></svg>,
  route: <svg {...svg}><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="6" r="2.5" /><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5" /></svg>,
  me: <svg {...svg}><circle cx="12" cy="8" r="4" /><path d="M4 21c1-4 4-6 8-6s7 2 8 6" /></svg>,
  city: <svg {...svg}><path d="M4 21V9l6-4v16M10 21V12l10-3v12M3 21h18" /></svg>,
  sun: <svg {...svg}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  moon: <svg {...svg}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>,
  pin: <svg {...svg}><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11Z" /><circle cx="12" cy="10" r="2.4" /></svg>,
  window: <svg {...svg}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M12 3v18M4 12h16" /></svg>,
  walk: <svg {...svg}><circle cx="13" cy="4.5" r="2" /><path d="M9 21l2.5-6-2-3 3-3 2.5 3 3 1M11.5 15l3 2.5V21M9.5 9.5 7 12" /></svg>,
  fan: <svg {...svg}><circle cx="12" cy="12" r="1.8" /><path d="M12 10.2C12 6 14 3.5 16.5 4.5c2 .8 1.4 4-1.2 5.3M13.8 12c4.200 0 6.700 2 5.700 4.500-.8 2-4 1.400-5.300-1.200M12 13.800c0 4.200-2 6.700-4.500 5.700-2-.8-1.400-4 1.200-5.300M10.200 12C6 12 3.500 10 4.500 7.500c.8-2 4-1.400 5.300 1.200" /></svg>,
  clock: <svg {...svg}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>,
  thermo: <svg {...svg}><path d="M14 14.8V5a2 2 0 0 0-4 0v9.800a4 4 0 1 0 4 0Z" /></svg>,
  leaf: <svg {...svg}><path d="M5 19c0-8 5-13 14-14 0 9-5 14-12 14-1 0-2 0-2 0Z" /><path d="M5 19c2-5 5-8 9-10" /></svg>,
  locate: <svg {...svg}><path d="M12 3l7 18-7-4-7 4z" /></svg>,
  expand: <svg {...svg}><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>,
  play: <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15l13-7.5z" /></svg>,
  pause: <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 4h4v16H6zM14 4h4v16h-4z" /></svg>,
};

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <path d="M9 21c0-6 4-10 12-11 0 7-3 11-9 11-1 0-2 0-3 0Z" fill="var(--brand-ink)" />
      <path d="M9 24c2-5 5-8 9-10" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" fill="none" />
    </svg>
  );
}

/** Collapsible section: the detail that used to sit open on the page now waits behind one line. */
export function Acc({ title, children, open }: { title: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details className="acc" open={open}>
      <summary>{title}</summary>
      <div className="acc-body">{children}</div>
    </details>
  );
}

/** Controls that sit on the map itself (top left), built from small grouped buttons. */
export const McGroup = ({ label, children }: { label: string; children: ReactNode }) => <div className="mc-group" role="group" aria-label={label}>{children}</div>;
export const McBtn = ({ on, onClick, children, title, label }: { on: boolean; onClick: () => void; children: ReactNode; title?: string; label?: string }) => (
  <button className="mc-btn" aria-pressed={on} onClick={onClick} title={title} aria-label={label}>{children}</button>
);
