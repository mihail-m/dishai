import type { Advice, Dashboard, DistrictsFC, Forecast, ForecastWindow, Profile, Scenario, SensorsResp, Toggle } from "./types";

const q = (s: Scenario) => (s === "live" ? "" : `scenario=${s}`);

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `${r.status} ${url}`);
  return r.json();
}

export const api = {
  window: async (s: Scenario, refresh: boolean, signal: AbortSignal): Promise<ForecastWindow> => {
    type Wire = Omit<ForecastWindow, "values"> & { values: (Omit<CaiValues, "cai"> & { cai_base64: string })[] };
    const result = await j<Wire>(`/api/forecast/${refresh ? "refresh" : "window"}?${q(s)}`, { method: refresh ? "POST" : "GET", signal, cache: "no-store" });
    const n = result.hours.length;
    if (n < 24 || result.values.length !== n || result.forecast.hours.length !== n) throw new Error("Incomplete forecast. Please refresh again.");
    return { ...result, values: result.values.map(({ cai_base64, ...v }) => ({ ...v, cai: Uint8Array.from(atob(cai_base64), (c) => c.charCodeAt(0)) })) };
  },
  forecast: (s: Scenario) => j<Forecast>(`/api/forecast?${q(s)}`),
  sensors: (s: Scenario) => j<SensorsResp>(`/api/sensors?${q(s)}`),
  districts: (s: Scenario) => j<DistrictsFC>(`/api/districts?${q(s)}`),
  advice: (p: Profile, d: string, s: Scenario, toggles: Toggle[] = []) =>
    j<Advice>(`/api/advice?profile=${p}&district=${encodeURIComponent(d)}&${[q(s), ...toggles.map((t) => `toggles=${t}`)].filter(Boolean).join("&")}`),
  dashboard: (s: Scenario) => j<Dashboard>(`/api/dashboard?${q(s)}`),
  lead: (district: string, heating_type: string, likely_eligible: boolean) =>
    j<{ ok: boolean; district_total: number; message: string }>("/api/leads", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ district, heating_type, likely_eligible }),
    }),
};

export const hhmm = (t: string) => t.slice(11, 16);
export const dayHour = (t: string) => `${t.slice(8, 10)}.${t.slice(5, 7)} ${t.slice(11, 16)}`;
/** "Sat 23:00" style label for an ISO local time. */
export const dayTime = (t: string) => `${new Date(t).toLocaleDateString("en-GB", { weekday: "short" })} ${t.slice(11, 16)}`;
export const clamp = (x: number, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, x));
export const levelOf = (s: number) => (s >= 60 ? "warning" : s >= 35 ? "watch" : "good") as "good" | "watch" | "warning";
export const LEVEL_TEXT = { good: "Good", watch: "Watch", warning: "Inversion warning" } as const;  // stagnation score
export const PM_LEVEL_TEXT = { good: "Fine", watch: "Sensitive groups", warning: "Everyone" } as const;  // PM2.5 alert audience
export const PM_WATCH = 15;  // WHO 24 h guideline
export const PM_WARNING = 25;  // EU 24 h limit

import type { AreasResp, CaiValues, EdgeExplain, GoogleAir, IncidentsResp, Mode, No2Resp, NetworkResp, ReferenceResp, RouteResp, TrafficResp, Validation } from "./types";
export interface ForecastRequest { forecastId?: string; signal?: AbortSignal; includeGoogle?: boolean; heatScale?: "absolute" | "relative" }
const snapshotQuery = (opts?: ForecastRequest) => opts?.forecastId ? `&forecast_id=${encodeURIComponent(opts.forecastId)}` : "";
export const api2 = {
  values: (s: Scenario, hour?: number) => j<CaiValues>(`/api/cai/values?${[q(s), hour != null ? `hour=${hour}` : ""].filter(Boolean).join("&")}`),
  edge: (id: number, s: Scenario, hour?: number, opts?: ForecastRequest) =>
    j<EdgeExplain>(`/api/cai/edge?id=${id}&${[q(s), hour != null ? `hour=${hour}` : ""].filter(Boolean).join("&")}${snapshotQuery(opts)}`, { signal: opts?.signal }),
  route: async (from: [number, number], to: [number, number], lam: number, s: Scenario, hour?: number, mode: Mode = "walk", opts?: ForecastRequest): Promise<RouteResp> => {
    const url = `/api/route?from=${from.join(",")}&to=${to.join(",")}&lambda=${lam}&mode=${mode}${hour != null ? `&hour=${hour}` : ""}${s === "live" ? "" : `&scenario=${s}`}${snapshotQuery(opts)}${opts?.includeGoogle === false ? "&include_google=false" : ""}`;
    const r = await fetch(url, { signal: opts?.signal });
    if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `${r.status}`);
    return r.json();
  },
  network: (s: Scenario, hour?: number, mode: Mode = "walk", opts?: ForecastRequest) => j<NetworkResp>(`/api/network?${[q(s), hour != null ? `hour=${hour}` : "", `mode=${mode}`].filter(Boolean).join("&")}${snapshotQuery(opts)}`, { signal: opts?.signal }),
  areas: (s: Scenario, hour: number | undefined, mode: Mode, metric: "air" | "heat", opts?: ForecastRequest) =>
    j<AreasResp>(`/api/cai/areas?${[q(s), hour != null ? `hour=${hour}` : "", `mode=${mode}`, `metric=${metric}`, ...(metric === "heat" && opts?.heatScale ? [`scale=${opts.heatScale}`] : [])].filter(Boolean).join("&")}${snapshotQuery(opts)}`, { signal: opts?.signal }),
  reference: (s: Scenario) => j<ReferenceResp>(`/api/reference?${q(s)}`),
  validation: (s: Scenario) => j<Validation>(`/api/validation?${q(s)}`),
  traffic: () => j<TrafficResp>("/api/traffic", { cache: "no-store" }),
  incidents: () => j<IncidentsResp>("/api/traffic/incidents", { cache: "no-store" }),
  googleAir: () => j<GoogleAir>("/api/google-air"),
  no2: () => j<No2Resp>("/api/no2"),
};
