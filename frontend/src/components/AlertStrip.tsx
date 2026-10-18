import { useState } from "react";
import { KIND_LABEL, LEVELS, disableNotifications, enableNotifications, notificationsSupported, rank, sendTest, updateNotifications, useLocalNotifications, useNotifyPrefs,
  type Alert, type AlertKind, type AlertLevel } from "../alerts";
import { Acc, Icon } from "./ui";

const TONE: Record<AlertLevel, { bg: string; fg: string; label: string }> = {
  warning: { bg: "var(--warn-bg)", fg: "var(--warn)", label: "Warning" },
  watch: { bg: "var(--watch-bg)", fg: "var(--watch)", label: "Watch" },
  info: { bg: "var(--surface-2)", fg: "var(--accent)", label: "Heads-up" },
};
const CONF: Record<Alert["confidence"], string> = { model: "Model forecast", forecast: "Weather forecast", inferred: "Inferred, indicative" };
const CAUSE: Record<string, string> = { dust: "desert dust", fire: "smoke from fires", local: "local heating and traffic in still air" };
const ICON: Record<AlertKind, keyof typeof Icon> = { pm: "window", heat: "thermo", dust: "fan", fire: "clock", transport: "route" };
const LEVEL_WORD: Record<AlertLevel, string> = { info: "Any alert", watch: "Watch and warning", warning: "Warnings only" };

function AlertCard({ a }: { a: Alert }) {
  const t = TONE[a.level];
  return (
    <article className="rounded-2xl p-3" style={{ background: t.bg }} aria-label={`${t.label}: ${a.title}`}>
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: "var(--surface)", color: t.fg }}>{Icon[ICON[a.kind]]}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-extrabold uppercase tracking-wide" style={{ color: t.fg }}>{t.label} · {KIND_LABEL[a.kind]}</span>
            {a.in_progress && <span className="badge">now</span>}
          </div>
          <h3 className="display m-0 text-[15px] font-bold leading-snug">{a.title}</h3>
          <p className="muted m-0 mt-0.5 text-[13px] leading-snug">{a.message}</p>
          {a.cause.length > 0 && a.kind === "pm" && <p className="m-0 mt-1 text-xs"><b>Likely contributing:</b> {a.cause.map((c) => CAUSE[c] ?? c).join(", ")}</p>}
          <details className="mt-1.5">
            <summary className="cursor-pointer text-xs font-bold" style={{ color: t.fg }}>What to do</summary>
            <ul className="m-0 mt-1 grid list-none gap-1 p-0">
              {a.actions.map((x) => <li key={x.id} className="text-[13px] leading-snug"><b>{x.title}.</b>{x.body ? <span className="muted"> {x.body}</span> : null}</li>)}
            </ul>
            <p className="faint m-0 mt-1.5 text-[11px]">{CONF[a.confidence]} · {a.source}</p>
            {typeof a.detail?.source_url === "string" && /^https?:\/\//.test(a.detail.source_url) && <p className="m-0 mt-1 text-[11px]"><a href={a.detail.source_url} target="_blank" rel="noreferrer noopener">Announcement</a></p>}
          </details>
        </div>
      </div>
    </article>
  );
}

export function NotificationSettings({ alerts }: { alerts: Alert[] }) {
  const [prefs, setPrefs] = useNotifyPrefs();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useLocalNotifications(alerts, prefs);
  const run = async (f: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setMsg(null);
    try { await f(); if (ok) setMsg(ok); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const toggleKind = (k: AlertKind) => {
    const kinds = prefs.kinds.includes(k) ? prefs.kinds.filter((x) => x !== k) : [...prefs.kinds, k];
    if (!kinds.length) return;
    const next = { ...prefs, kinds };
    setPrefs(next);
    if (prefs.enabled) void run(() => updateNotifications(next));
  };
  const setLevel = (level: AlertLevel) => {
    const next = { ...prefs, level };
    setPrefs(next);
    if (prefs.enabled) void run(() => updateNotifications(next));
  };
  if (!notificationsSupported()) return <p className="faint m-0 text-xs">This browser cannot show notifications. On iPhone, add dishai to the Home Screen first.</p>;
  return (
    <div className="grid gap-2.5">
      <p className="muted m-0 text-[13px]">Get a message when air, heat, dust or smoke needs your attention. Nothing about you is stored: only the notification address of this browser and the choices below.</p>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Alert types">
        {(Object.keys(KIND_LABEL) as AlertKind[]).map((k) => <button key={k} className={`chip ${prefs.kinds.includes(k) ? "on" : ""}`} aria-pressed={prefs.kinds.includes(k)} onClick={() => toggleKind(k)}>{KIND_LABEL[k]}</button>)}
      </div>
      <label className="flex items-center gap-2 text-sm font-semibold">
        Notify me for
        <select value={prefs.level} onChange={(e) => setLevel(e.target.value as AlertLevel)} aria-label="Minimum alert level">
          {LEVELS.map((l) => <option key={l} value={l}>{LEVEL_WORD[l]}</option>)}
        </select>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        {!prefs.enabled
          ? <button className="btn btn-primary" disabled={busy} onClick={() => void run(async () => setPrefs(await enableNotifications(prefs.kinds, prefs.level)))}>{busy ? "Turning on…" : "Turn on notifications"}</button>
          : <>
              <button className="btn" disabled={busy} onClick={() => void run(() => sendTest(), "Test sent.")}>Send a test</button>
              <button className="btn" disabled={busy} onClick={() => void run(async () => setPrefs(await disableNotifications()))}>Turn off</button>
            </>}
      </div>
      {prefs.enabled && (
        <p className="faint m-0 text-xs" role="status">
          {prefs.mode === "push" ? "On. Messages reach this device even when dishai is closed." : "On while dishai is open in this browser. Notifications with the page closed need the server's push keys (docs/ALERTS.md)."}
        </p>
      )}
      {msg && <p className="m-0 text-xs" role="status" style={{ color: "var(--warn)" }}>{msg}</p>}
    </div>
  );
}

/** The things that need attention in the next 48 hours, most severe first, and the notification settings behind one line. */
export default function AlertStrip({ alerts, settings = true }: { alerts: Alert[]; settings?: boolean }) {
  const shown = [...alerts].sort((a, b) => rank(b.level) - rank(a.level)).slice(0, 4);
  return (
    <section aria-label="Alerts" className="grid gap-2">
      {shown.map((a) => <AlertCard key={`${a.id}:${a.level}`} a={a} />)}
      {alerts.length > shown.length && <p className="faint m-0 text-xs">{alerts.length - shown.length} more alert{alerts.length - shown.length > 1 ? "s" : ""} not shown.</p>}
      {settings && <Acc title="Notifications"><NotificationSettings alerts={alerts} /></Acc>}
    </section>
  );
}
