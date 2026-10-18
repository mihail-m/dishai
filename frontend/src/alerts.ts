import { createContext, useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { Scenario } from "./types";

export type AlertKind = "pm" | "heat" | "dust" | "fire";
export type AlertLevel = "info" | "watch" | "warning";
export const KIND_LABEL: Record<AlertKind, string> = { pm: "Air quality", heat: "Heat", dust: "Dust", fire: "Smoke" };
export const LEVELS: AlertLevel[] = ["info", "watch", "warning"];
export interface AlertAction { id: string; title: string; body: string }
export interface Alert {
  id: string; kind: AlertKind; level: AlertLevel; title: string; message: string; start: string; end: string; in_progress: boolean;
  peak: { value: number; unit: string; time: string | null } | null; cause: string[]; source: string; confidence: "model" | "forecast" | "inferred";
  audience: "everyone" | "sensitive"; actions: AlertAction[];
}
export interface AlertsResp {
  generated_at: string; scenario: string | null; alerts: Alert[];
  sources: { forecast?: string; cams_dust?: string; fires?: { enabled: boolean; reason?: string; error?: string } }; push: { enabled: boolean };
}
export const rank = (l: AlertLevel) => LEVELS.indexOf(l);

/** Alerts shown by the Explore panel, so the suggestions below the banner can lead with what to do about them. */
export const AlertsContext = createContext<Alert[]>([]);

// ---- one shared poll per scenario ----------------------------------------------------------------------------------------------------------
interface Slot { data: AlertsResp | null; error: string | null; at: number; busy: boolean; listeners: Set<() => void>; timer: ReturnType<typeof setInterval> | null }
const FRESH_MS = 60_000;
const slots = new Map<string, Slot>();
const slotOf = (s: Scenario): Slot => slots.get(s) ?? (slots.set(s, { data: null, error: null, at: 0, busy: false, listeners: new Set(), timer: null }).get(s)!);

async function pull(s: Scenario) {
  const slot = slotOf(s);
  if (slot.busy) return; // React StrictMode mounts twice in development: one request is enough
  slot.busy = true;
  try {
    const r = await fetch(`/api/alerts${s === "live" ? "" : `?scenario=${s}`}`, { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    slot.data = await r.json(); slot.error = null; slot.at = Date.now();
  } catch (e) { slot.error = String(e); }
  slot.busy = false;
  slot.listeners.forEach((f) => f());
}

/** Alerts for the next 48 hours, refreshed every 5 minutes; every component shares one request. */
export function useAlerts(scenario: Scenario) {
  const slot = slotOf(scenario);
  // the subscription must keep its identity between renders, or React re-subscribes (and would re-request) every time
  const sub = useCallback((f: () => void) => {
    slot.listeners.add(f);
    if (slot.listeners.size === 1) {
      if (Date.now() - slot.at > FRESH_MS) void pull(scenario);
      slot.timer = setInterval(() => void pull(scenario), 5 * 60 * 1000);
    }
    return () => { slot.listeners.delete(f); if (!slot.listeners.size && slot.timer) { clearInterval(slot.timer); slot.timer = null; } };
  }, [slot, scenario]);
  const data = useSyncExternalStore(sub, () => slot.data);
  const error = useSyncExternalStore(sub, () => slot.error);
  return { data, alerts: data?.alerts ?? [], error, loading: !data && !error };
}

// ---- notification settings (this browser only) ---------------------------------------------------------------------------------------------
export interface NotifyPrefs { enabled: boolean; kinds: AlertKind[]; level: AlertLevel; mode: "push" | "page" }
const PREFS_KEY = "dishai.alerts", SEEN_KEY = "dishai.alerts.seen";
const DEFAULT: NotifyPrefs = { enabled: false, kinds: ["pm", "heat", "dust", "fire"], level: "watch", mode: "page" };

export function loadNotifyPrefs(): NotifyPrefs {
  try { return { ...DEFAULT, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") }; } catch { return DEFAULT; }
}
export function saveNotifyPrefs(p: NotifyPrefs) { try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch { /* private mode: the choice is just not remembered */ } }

export const notificationsSupported = () => typeof window !== "undefined" && "Notification" in window && "serviceWorker" in navigator;

async function registration() {
  return navigator.serviceWorker.register("/sw.js").then(() => navigator.serviceWorker.ready);
}
const b64ToBytes = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
async function post(url: string, body: unknown) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? String(r.status));
}

/** Ask permission, register the service worker and, if the server has push keys, subscribe. Without them alerts appear while the page is open. */
export async function enableNotifications(kinds: AlertKind[], level: AlertLevel): Promise<NotifyPrefs> {
  if (!notificationsSupported()) throw new Error("This browser cannot show notifications.");
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications are blocked. Allow them for this site in the browser settings.");
  const reg = await registration();
  let mode: NotifyPrefs["mode"] = "page";
  try {
    const key = await (await fetch("/api/push/key")).json() as { enabled: boolean; public_key: string | null };
    if (key.enabled && key.public_key && "PushManager" in window) {
      const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key.public_key) as BufferSource }));
      await post("/api/push/subscribe", { subscription: sub.toJSON(), kinds, min_level: level });
      mode = "push";
    }
  } catch { mode = "page"; } // no push service reachable (private window, blocked, offline): the in-page alerts still work
  const prefs = { enabled: true, kinds, level, mode };
  saveNotifyPrefs(prefs);
  return prefs;
}

export async function updateNotifications(prefs: NotifyPrefs): Promise<NotifyPrefs> {
  saveNotifyPrefs(prefs);
  if (prefs.mode === "push") {
    const sub = await (await registration()).pushManager.getSubscription();
    if (sub) await post("/api/push/subscribe", { subscription: sub.toJSON(), kinds: prefs.kinds, min_level: prefs.level });
  }
  return prefs;
}

export async function disableNotifications(): Promise<NotifyPrefs> {
  try {
    const sub = await (await navigator.serviceWorker.getRegistration())?.pushManager.getSubscription();
    if (sub) { await post("/api/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => undefined); await sub.unsubscribe(); }
  } catch { /* nothing to undo */ }
  const prefs = { ...loadNotifyPrefs(), enabled: false };
  saveNotifyPrefs(prefs);
  return prefs;
}

export async function sendTest(): Promise<void> {
  const sub = await (await navigator.serviceWorker.getRegistration())?.pushManager.getSubscription();
  if (sub) return post("/api/push/test", { endpoint: sub.endpoint });
  const reg = await registration();
  await reg.showNotification("DishAI alerts are on", { body: "You will get a message like this when air or heat needs your attention.", tag: "test", icon: "/icon.svg" });
}

/** While the page is open and push is not in use, show each new (alert, level) once as a notification. */
export function useLocalNotifications(alerts: Alert[], prefs: NotifyPrefs) {
  useEffect(() => {
    if (!prefs.enabled || prefs.mode !== "page" || !notificationsSupported() || Notification.permission !== "granted") return;
    let seen: string[] = [];
    try { seen = JSON.parse(localStorage.getItem(SEEN_KEY) ?? "[]"); } catch { /* start over */ }
    const fresh = alerts.filter((a) => prefs.kinds.includes(a.kind) && rank(a.level) >= rank(prefs.level) && !seen.includes(`${a.id}:${a.level}`));
    if (!fresh.length) return;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify([...seen, ...fresh.map((a) => `${a.id}:${a.level}`)].slice(-100))); } catch { /* shown again next time */ }
    void registration().then((reg) => fresh.forEach((a) => reg.showNotification(a.title, { body: a.message, tag: a.id, icon: "/icon.svg", requireInteraction: a.level === "warning" })));
  }, [alerts, prefs]);
}

/** Convenience for components that own the notification settings. */
export function useNotifyPrefs() {
  const [prefs, setPrefs] = useState(loadNotifyPrefs);
  return [prefs, setPrefs] as const;
}
