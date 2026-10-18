import { useContext, useMemo, type ReactNode } from "react";
import { AlertsContext, rank, type Alert } from "../alerts";
import type { AreasResp, Forecast } from "../types";
import { PM_WARNING, PM_WATCH, hhmm } from "../api";
import { distanceKm, type LocStatus, type Place } from "../location";
import { Icon } from "./ui";

type Tone = "good" | "watch" | "warn" | "info";
export interface Suggestion { id: string; tone: Tone; icon: keyof typeof Icon; title: string; body: string }

interface Input {
  pm: number | null; score: number | null; fc: Forecast | null; hourIndex: number;
  located: boolean; better: { name: string; km: number; score: number } | null;
}

/** Plain-language advice from the PM2.5 around the user (or the city, without a location), the next hours and the heat. */
export function suggest({ pm, score, fc, hourIndex, located, better }: Input): Suggestion[] {
  const out: Suggestion[] = [];
  const hours = fc?.hours ?? [];
  const pmOf = (i: number) => hours[i]?.pm25_model ?? hours[i]?.pm25 ?? null;
  const city = pmOf(hourIndex);
  const now = pm ?? city;
  if (now == null) return out;
  // what the next 24 hours add to the picture, relative to the city forecast at the selected hour
  const ahead = hours.slice(hourIndex + 1, hourIndex + 25).map((h, k) => ({ i: hourIndex + 1 + k, v: h.pm25_model ?? h.pm25 })).filter((x): x is { i: number; v: number } => x.v != null);
  const worse = ahead.filter((x) => x.v > PM_WARNING);
  const peak = ahead.length ? ahead.reduce((a, b) => (b.v > a.v ? b : a)) : null;
  const better_at = ahead.find((x) => x.v <= PM_WATCH);
  const level: Tone = now <= PM_WATCH ? "good" : now <= PM_WARNING ? "watch" : "warn";
  const where = located ? "around you" : "in the city";
  const bw = fc?.best_window;

  if (level === "good") {
    out.push({ id: "open", tone: "good", icon: "window", title: "Open your windows",
      body: `Air ${where} is clean (PM2.5 ${Math.round(now)} µg/m³). A good moment to air your home.${worse.length ? ` Do it before ${hhmm(hours[worse[0].i].time)}, when the air gets worse.` : ""}` });
    out.push({ id: "walk", tone: "good", icon: "walk", title: "Good time for a walk or errands",
      body: `Fine for a walk, a run or grocery shopping on foot.${bw ? ` Cleanest stretch in the next 24 hours: ${bw.start_hhmm} to ${bw.end_hhmm}.` : ""}` });
  } else if (level === "watch") {
    out.push({ id: "air", tone: "watch", icon: "window", title: "Air your home briefly",
      body: `PM2.5 ${where} is ${Math.round(now)} µg/m³. Ventilate for 5 to 10 minutes, away from busy roads, then close up.` });
    out.push({ id: "walk", tone: "watch", icon: "walk", title: "Short outdoor trips are fine",
      body: "Prefer parks and quiet streets. People with asthma, older people and small children should keep it short." });
  } else {
    out.push({ id: "closed", tone: "warn", icon: "window", title: "Keep your windows closed",
      body: `PM2.5 ${where} is ${Math.round(now)} µg/m³, above the EU daily limit of ${PM_WARNING}.` });
    out.push({ id: "purifier", tone: "warn", icon: "fan", title: "Turn on your air purifier",
      body: "If you have one, run it in the room you use most. Cooking, candles and burning wood add to indoor smoke." });
    out.push({ id: "skip", tone: "warn", icon: "walk", title: "Skip outdoor exercise",
      body: "If you have to go out, take a park or a quiet street and consider an FFP2 mask." });
  }

  if (level !== "warn" && worse.length && peak) {
    out.push({ id: "worse", tone: "watch", icon: "clock", title: `Air gets worse from ${hhmm(hours[worse[0].i].time)}`,
      body: `Expected peak ${Math.round(peak.v)} µg/m³ around ${hhmm(hours[peak.i].time)}. Plan your walks and airing before then.` });
  } else if (level === "warn" && better_at) {
    out.push({ id: "improves", tone: "info", icon: "clock", title: `Improves from ${hhmm(hours[better_at.i].time)}`,
      body: "Wait until then to air your home or head out for exercise." });
  }

  const feels = hours[hourIndex]?.apparent ?? hours[hourIndex]?.t2m ?? null;
  if (feels != null && feels >= 27) {
    out.push({ id: "heat", tone: feels >= 32 ? "warn" : "watch", icon: "thermo", title: feels >= 32 ? "It is hot out" : "A warm day",
      body: `Feels like ${Math.round(feels)} °C. Ventilate at night and early morning, keep blinds closed in the afternoon and drink water.` });
  }
  if (located && better && score != null && better.score >= score + 12) {
    out.push({ id: "nearby", tone: "info", icon: "leaf", title: "Cleaner air nearby",
      body: `Near ${better.name}, ${better.km.toFixed(1)} km away, the score is ${Math.round(better.score)} against ${Math.round(score)} where you are.` });
  }
  return out;
}

const ALERT_ICON: Record<Alert["kind"], keyof typeof Icon> = { pm: "window", heat: "thermo", dust: "fan", fire: "clock" };
const LEAD_H = 12;

/** The first thing to do about each alert that is under way or starts within 12 hours; these lead the list. */
export function alertSuggestions(alerts: Alert[], now: string | null): Suggestion[] {
  const t0 = now ? new Date(`${now}:00`).getTime() : Date.now();
  return alerts
    .filter((a) => rank(a.level) >= rank("watch") && a.actions.length && new Date(`${a.end}:00`).getTime() > t0 && new Date(`${a.start}:00`).getTime() - t0 <= LEAD_H * 3_600_000)
    .map((a): Suggestion => ({ id: `alert-${a.id}`, tone: a.level === "warning" ? "warn" : "watch", icon: ALERT_ICON[a.kind], title: a.actions[0].title, body: `${a.title}. ${a.actions[0].body || a.message}`.trim() }));
}

function nearestCell(a: AreasResp | null, p: Place) {
  if (!a) return null;
  const c = a.cells, kx = Math.cos((p.lat * Math.PI) / 180);
  let best = -1, bd = Infinity;
  for (let i = 0; i < c.lon.length; i++) {
    const dx = (c.lon[i] - p.lon) * kx, dy = c.lat[i] - p.lat, d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = i; }
  }
  if (best < 0) return null;
  const m = distanceKm(p.lat, p.lon, c.lat[best], c.lon[best]) * 1000;
  return m <= Math.max(1500, a.cell_m * 1.5) ? { score: c.mean[best], pm: c.pm25?.[best] ?? null } : null;
}

interface Props {
  areas: AreasResp | null; fc: Forecast | null; hourIndex: number;
  status: LocStatus; place: Place | null; district: string | null; onLocate: () => void; onStop: () => void;
  note?: ReactNode;
}

export default function Suggestions({ areas, fc, hourIndex, status, place, district, onLocate, onStop }: Props) {
  const here = useMemo(() => (place ? nearestCell(areas, place) : null), [areas, place]);
  const better = useMemo(() => {
    if (!place || !areas) return null;
    const near = areas.extremes.best.map((b) => ({ name: b.place.replace(/^near /, ""), km: distanceKm(place.lat, place.lon, b.lat, b.lon), score: b.value })).filter((b) => b.km <= 6);
    return near.sort((a, b) => a.km - b.km)[0] ?? null;
  }, [areas, place]);
  const alerts = useContext(AlertsContext);
  const items = useMemo(() => {
    const lead = alertSuggestions(alerts, fc?.hours[fc.now_index]?.time ?? null);
    const heatAlert = alerts.some((a) => a.kind === "heat" && rank(a.level) >= rank("watch"));
    const own = suggest({ pm: here?.pm ?? null, score: here?.score ?? null, fc, hourIndex, located: !!place && !!here, better }).filter((s) => !(heatAlert && s.id === "heat"));
    return [...lead, ...own];
  }, [alerts, here, fc, hourIndex, place, better]);

  return (
    <section aria-label="Suggestions for you" className="grid gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="eyebrow m-0">Where you are</h3>
        {status === "ok" && <button className="text-xs font-bold" style={{ color: "var(--accent)" }} onClick={onStop}>Stop sharing</button>}
      </div>
      {status === "ok" && place ? (
        <div className="flex items-center gap-2 text-sm">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full" style={{ background: "var(--brand-soft)", color: "var(--accent)" }}>{Icon.pin}</span>
          <span className="min-w-0"><b>{district ?? "Your location"}</b>
            <span className="muted">{here ? ` · air quality score ${Math.round(here.score)} here` : " · outside the mapped streets, showing city-wide air"}</span></span>
        </div>
      ) : (
        <div className="rounded-2xl p-3" style={{ background: "var(--surface-2)" }}>
          <p className="m-0 text-sm"><b>Advice for where you are.</b> <span className="muted">{status === "denied" ? "Location is blocked in your browser. Allow it for this site, or the advice below uses the whole city." : status === "unavailable" ? "Your location is not available right now. The advice below uses the whole city." : "Share your location to base it on the air around you. It stays in this tab and is never stored or sent."}</span></p>
          {status !== "denied" && <button className="btn btn-primary mt-2" onClick={onLocate} disabled={status === "asking"}>{status === "asking" ? "Finding you…" : "Use my location"}</button>}
        </div>
      )}
      <ul className="m-0 grid list-none gap-2 p-0">
        {items.map((s) => (
          <li key={s.id} className="flex gap-3 rounded-2xl p-3" style={{ background: s.tone === "info" ? "var(--surface-2)" : `var(--${s.tone}-bg)` }}>
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: "var(--surface)", color: s.tone === "info" ? "var(--accent)" : `var(--${s.tone})` }}>{Icon[s.icon]}</span>
            <div className="min-w-0">
              <h4 className="display m-0 text-[15px] font-bold leading-snug">{s.title}</h4>
              <p className="muted m-0 mt-0.5 text-[13px] leading-snug">{s.body}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
