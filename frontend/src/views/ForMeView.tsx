import { useEffect, useMemo, useState } from "react";
import type { Advice, AreasResp, Card, DistrictsFC, Forecast, Hour, Profile, Scenario, Species, Toggle } from "../types";
import { PM_WARNING, PM_WATCH, api, api2, hhmm } from "../api";
import { rank, useAlerts, type Alert } from "../alerts";
import { distanceKm, districtAt, useLocation } from "../location";
import AlertStrip, { NotificationSettings } from "../components/AlertStrip";
import PlumeCard from "../components/PlumeCard";
import Wizard from "../components/Wizard";
import { Acc, Icon } from "../components/ui";

type Tone = "good" | "watch" | "warn";
const TONE: Record<Tone, { bg: string; fg: string }> = { good: { bg: "var(--good-bg)", fg: "var(--good)" }, watch: { bg: "var(--watch-bg)", fg: "var(--watch)" }, warn: { bg: "var(--warn-bg)", fg: "var(--warn)" } };

const PROFILES: { id: Profile; label: string; hint: string }[] = [
  { id: "driver", label: "I drive in the city", hint: "Adds low-emission zone and public transport tips" },
  { id: "wood_heater", label: "I heat with wood or coal", hint: "Adds tips for when to hold off the stove" },
  { id: "vulnerable", label: "I am older or health-sensitive", hint: "Adds a stay-in plan and when to air the home" },
];
const TOGGLES: { id: Toggle; label: string; hint: string }[] = [
  { id: "asthma", label: "Asthma", hint: "Combines air, heat and pollen triggers" },
  { id: "pollen_allergy", label: "Pollen allergy", hint: "Adds pollen levels and timing" },
  { id: "heat_sensitive", label: "Sensitive to heat", hint: "Heart or health conditions, pregnancy, some medication" },
  { id: "outdoor_exercise", label: "I exercise outdoors", hint: "Finds the best slot for running or cycling" },
  { id: "kids_or_elderly", label: "I look after kids or older relatives", hint: "A plan for the people you look after" },
];
const ICO = "inline-flex h-4 w-4 shrink-0 [&>svg]:h-full [&>svg]:w-full";
const SPECIES = ["alder", "birch", "grass", "mugwort", "olive", "ragweed"] as const;

// ---- choices, kept in this browser (same key and shape as before, so earlier choices carry over) ---------------------------------------------
const KEY = "dishai.foryou";
interface Prefs { profile: Profile; toggles: Toggle[]; species: string[]; saved: boolean }
function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    const p = JSON.parse(raw ?? "{}");
    const ok = <T extends string>(a: unknown, all: readonly string[]): T[] => (Array.isArray(a) ? a.filter((x): x is T => all.includes(x)) : []);
    return { profile: PROFILES.some((x) => x.id === p.profile) ? p.profile : "driver", toggles: ok<Toggle>(p.toggles, TOGGLES.map((t) => t.id)), species: ok<string>(p.species, SPECIES), saved: raw != null };
  } catch { return { profile: "driver", toggles: [], species: [], saved: false }; }
}
function savePrefs(p: Prefs) {
  try { localStorage.setItem(KEY, JSON.stringify({ profile: p.profile, toggles: p.toggles, species: p.species })); } catch { /* private mode: not remembered */ }
}

// ---- how an hour feels: air and heat together ----------------------------------------------------------------------------------------------
function hourTone(h: Hour): Tone {
  const pm = h.pm25_model ?? h.pm25, feels = h.apparent ?? h.t2m;
  if ((pm != null && pm > PM_WARNING) || feels >= 38) return "warn";
  if ((pm != null && pm > PM_WATCH) || feels >= 30) return "watch";
  return "good";
}
const hourLine = (h: Hour) => {
  const pm = h.pm25_model ?? h.pm25;
  return `${hhmm(h.time)} · ${pm != null ? `PM2.5 ${Math.round(pm)} µg/m³ · ` : ""}feels like ${Math.round(h.apparent ?? h.t2m)} °C`;
};

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

interface Verdict { tone: Tone; headline: string; line: string }
const HEADLINE: Record<Alert["kind"], Record<"warning" | "watch", string>> = {
  pm: { warning: "Poor air: limit time outside", watch: "Air may turn poor" },
  heat: { warning: "Dangerous heat today", watch: "Hot day: take care" },
  dust: { warning: "Heavy dust in the air", watch: "Dust in the air" },
  fire: { warning: "Smoke in the air", watch: "Smoke may reach the city" },
  transport: { warning: "Leave the car at home", watch: "Green ticket: ride public transport" },
};
function verdict(alerts: Alert[], hour: Hour | undefined): Verdict {
  const top = [...alerts].filter((a) => rank(a.level) >= rank("watch")).sort((a, b) => Number(b.in_progress) - Number(a.in_progress) || rank(b.level) - rank(a.level))[0];
  if (top) return { tone: top.level === "warning" ? "warn" : "watch", headline: HEADLINE[top.kind][top.level as "warning" | "watch"], line: top.message };
  const tone = hour ? hourTone(hour) : "good";
  if (tone === "warn") return { tone, headline: "Air or heat is hard on the body right now", line: hour ? hourLine(hour) : "" };
  if (tone === "watch") return { tone, headline: "Fine for most people; take it easy if you are sensitive", line: hour ? hourLine(hour) : "" };
  return { tone, headline: "A good time to be outside", line: hour ? hourLine(hour) : "No alerts in the next 48 hours." };
}

function nearest(a: AreasResp | null, lat: number, lon: number): { v: number; offset?: number; pm?: number } | null {
  if (!a) return null;
  const c = a.cells, kx = Math.cos((lat * Math.PI) / 180);
  let best = -1, bd = Infinity;
  for (let i = 0; i < c.lon.length; i++) { const dx = (c.lon[i] - lon) * kx, dy = c.lat[i] - lat, d = dx * dx + dy * dy; if (d < bd) { bd = d; best = i; } }
  if (best < 0 || distanceKm(lat, lon, c.lat[best], c.lon[best]) * 1000 > Math.max(1500, a.cell_m * 1.5)) return null;
  return { v: c.mean[best], offset: c.offset?.[best], pm: c.pm25?.[best] };
}

function DayStrip({ fc }: { fc: Forecast }) {
  const hours = fc.hours.slice(fc.now_index, fc.now_index + 24);
  const [sel, setSel] = useState(0);
  const bw = fc.best_window;
  const inBest = (h: Hour) => !!bw && h.time >= bw.start && h.time < bw.end;
  const cur = hours[Math.min(sel, hours.length - 1)];
  if (!cur) return null;
  return (
    <section className="card grid gap-2 p-4" aria-label="The next 24 hours">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="m-0 text-base font-bold">The next 24 hours</h2>
        {bw ? <span className="muted text-xs">Best time outside: <b>{bw.start_hhmm}–{bw.end_hhmm}</b></span> : null}
      </div>
      <div className="flex gap-[3px]" role="group" aria-label="Hour by hour">
        {hours.map((h, i) => (
          <button key={h.time} onClick={() => setSel(i)} aria-pressed={i === sel} title={hourLine(h)} aria-label={hourLine(h)}
            className="h-10 min-w-0 flex-1 rounded-md" style={{ background: TONE[hourTone(h)].fg, opacity: i === sel ? 1 : 0.55, outline: inBest(h) ? "2px solid var(--ink)" : "none", outlineOffset: -2 }} />
        ))}
      </div>
      <div className="faint flex justify-between text-[11px] tabular-nums">{[0, 6, 12, 18, 23].map((i) => <span key={i}>{hours[i] ? hhmm(hours[i].time) : ""}</span>)}</div>
      <p className="muted m-0 text-[13px]" role="status">{hourLine(cur)}</p>
      <p className="faint m-0 text-[11px]">Green is comfortable, amber is hard on sensitive people, red is hard on everyone (air above the EU limit or feels-like above 38 °C). Outlined hours are the best window.</p>
    </section>
  );
}

function PlanItem({ title, body, tone, icon }: { title: string; body: string; tone: Tone | "info"; icon: keyof typeof Icon }) {
  const t = tone === "info" ? { bg: "var(--surface-2)", fg: "var(--accent)" } : TONE[tone];
  return (
    <li className="flex gap-3 rounded-2xl p-3" style={{ background: t.bg }}>
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: "var(--surface)", color: t.fg }}>{Icon[icon]}</span>
      <div className="min-w-0"><h3 className="display m-0 text-[15px] font-bold leading-snug">{title}</h3>{body && <p className="muted m-0 mt-0.5 text-[13px] leading-snug">{body}</p>}</div>
    </li>
  );
}
const CARD_ICON: Record<string, keyof typeof Icon> = { heat: "thermo", heat_shelter: "thermo", pollen: "leaf", hold_fire: "fan", stay_in: "window", ventilate: "window", shift_trips: "clock", activity: "walk" };
const ALERT_ICON: Record<Alert["kind"], keyof typeof Icon> = { pm: "window", heat: "thermo", dust: "fan", fire: "clock", transport: "route" };

interface Props { scenario: Scenario; districts: DistrictsFC | null; district: string | null; setDistrict: (d: string) => void; onPrefs?: (toggles: Toggle[], species: string[]) => void }

/** "For me": one verdict for where you are, a plan for the next 24 hours, then the setup and the city programmes. */
export default function ForMeView({ scenario, districts, district, setDistrict, onPrefs }: Props) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [fc, setFc] = useState<Forecast | null>(null);
  const [adv, setAdv] = useState<Advice | null>(null);
  const [airs, setAirs] = useState<AreasResp | null>(null);
  const [heats, setHeats] = useState<AreasResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { alerts } = useAlerts(scenario);
  const geo = useLocation();
  const names = useMemo(() => districts?.features.map((f) => f.properties.name).sort() ?? [], [districts]);

  useEffect(() => { savePrefs(prefs); onPrefs?.(prefs.toggles, prefs.species); }, [prefs]); // eslint-disable-line react-hooks/exhaustive-deps
  // a position fix picks the district; the choice then sticks across tabs
  useEffect(() => {
    if (geo.place && districts) { const d = districtAt(districts, geo.place.lat, geo.place.lon); if (d && d !== district) setDistrict(d); }
  }, [geo.place, districts]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let off = false;
    api.forecast(scenario).then((f) => !off && setFc(f)).catch((e) => !off && setErr(String(e)));
    api2.areas(scenario, undefined, "walk", "air").then((a) => !off && setAirs(a)).catch(() => undefined);
    api2.areas(scenario, undefined, "walk", "heat").then((a) => !off && setHeats(a)).catch(() => undefined);
    return () => { off = true; };
  }, [scenario]);
  useEffect(() => {
    if (!district) return;
    let off = false;
    api.advice(prefs.profile, district, scenario, prefs.toggles, prefs.toggles.includes("pollen_allergy") ? (prefs.species as Species[]) : []).then((a) => !off && setAdv(a)).catch((e) => !off && setErr(String(e)));
    return () => { off = true; };
  }, [prefs.profile, prefs.toggles, prefs.species, district, scenario]);

  const hour = fc?.hours[fc.now_index];
  const v = verdict(alerts, hour);
  const centroid = districts?.features.find((f) => f.properties.name === district)?.properties.centroid;
  const at = geo.place ? { lat: geo.place.lat, lon: geo.place.lon } : centroid ? { lat: centroid[1], lon: centroid[0] } : null;
  const here = at ? nearest(airs, at.lat, at.lon) : null;
  const feels = at ? nearest(heats, at.lat, at.lon) : null;

  // the plan: what to do about each active alert first, then the personal cards that are about health, then the rest
  const plan = useMemo(() => {
    const out: { id: string; title: string; body: string; tone: Tone | "info"; icon: keyof typeof Icon }[] = [];
    const seen = new Set<string>();
    const ranked = alerts.filter((x) => rank(x.level) >= rank("watch") && x.actions.length).sort((x, y) => rank(y.level) - rank(x.level) || Number(y.in_progress) - Number(x.in_progress));
    for (const a of ranked) {
      if (seen.has(a.kind)) continue; // one item per kind: the most severe
      seen.add(a.kind);
      out.push({ id: `a-${a.id}`, title: a.actions[0].title, body: `${a.title}. ${a.actions[0].body}`.trim(), tone: a.level === "warning" ? "warn" : "watch", icon: ALERT_ICON[a.kind] });
    }
    const personal = (adv?.cards ?? []).filter((c) => !c.cta && c.id !== "zone_lookup" && c.id !== "lez_check" && !(c.id === "heat" && seen.has("heat")));
    personal.sort((a, b) => Number(b.severity === "action") - Number(a.severity === "action"));
    for (const c of personal) if (!out.some((o) => o.title === c.title)) out.push({ id: c.id, title: c.title, body: c.body, tone: c.severity === "action" ? "watch" : "info", icon: CARD_ICON[c.id] ?? "leaf" });
    return out.slice(0, 6);
  }, [alerts, adv]);
  const programmes: Card[] = (adv?.cards ?? []).filter((c) => c.cta || c.id === "zone_lookup" || c.id === "lez_check");

  const flip = <T extends string>(list: T[], x: T) => (list.includes(x) ? list.filter((y) => y !== x) : [...list, x]);

  return (
    <div className="mx-auto grid max-w-[680px] gap-4">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="eyebrow m-0">{greeting()}</p>
          <h1 className="m-0 text-[1.6rem] font-bold">{district ?? "Sofia"}</h1>
        </div>
        <div className="flex items-center gap-2">
          {geo.status === "ok"
            ? <button className="btn" onClick={geo.stop}><span className={ICO}>{Icon.pin}</span> Using your location · stop</button>
            : <button className="btn" onClick={geo.request} disabled={geo.status === "asking"}><span className={ICO}>{Icon.locate}</span> {geo.status === "asking" ? "Finding you…" : "Use my location"}</button>}
          <select value={district ?? ""} onChange={(e) => setDistrict(e.target.value)} aria-label="District">{names.map((n) => <option key={n}>{n}</option>)}</select>
        </div>
      </header>
      {geo.status === "denied" && <p className="faint m-0 text-xs">Location is blocked in your browser; pick your district instead. Your position is never stored or sent.</p>}

      <section className="rounded-3xl p-5" style={{ background: TONE[v.tone].bg }} role="status" aria-live="polite" aria-label="Right now">
        <p className="eyebrow m-0" style={{ color: TONE[v.tone].fg }}>Right now{geo.place ? " where you are" : ` in ${district ?? "the city"}`}</p>
        <h2 className="display m-0 mt-1 text-[1.5rem] font-bold leading-tight">{v.headline}</h2>
        <p className="muted m-0 mt-1 text-sm">{v.line}</p>
        {(here || feels) && (
          <div className="mt-3 grid grid-cols-2 gap-2 text-center">
            {here && <div className="stat"><b style={{ fontSize: "1.9rem" }}>{Math.round(here.v)}</b><span>air quality score here (100 = cleanest)</span></div>}
            {feels && <div className="stat"><b style={{ fontSize: "1.9rem" }}>{Math.round(feels.v)}°</b>
              <span>feels like here{feels.offset != null && Math.abs(feels.offset) >= 0.5 ? ` (${feels.offset > 0 ? "+" : ""}${feels.offset.toFixed(1)}° vs city)` : ""}</span></div>}
          </div>
        )}
      </section>

      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>Could not load everything: {err}</div>}

      {alerts.length > 0 && <AlertStrip alerts={alerts} settings={false} />}

      <section aria-label="What to do">
        <h2 className="m-0 mb-2 text-base font-bold">What to do today</h2>
        {plan.length ? <ul className="m-0 grid list-none gap-2 p-0">{plan.map((p) => <PlanItem key={p.id} {...p} />)}</ul>
          : adv || !district ? <p className="muted m-0 text-sm">Nothing special: normal activity is fine.</p> : <div className="card h-24 animate-pulse" />}
      </section>

      {fc && <DayStrip fc={fc} />}

      <PlumeCard scenario={scenario} />

      <section className="card grid gap-3 p-4" aria-label="About you">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="m-0 text-base font-bold">Tailor this to you</h2>
          <span className="faint text-xs">Saved on this device only</span>
        </div>
        {!prefs.saved && <p className="muted m-0 text-sm">Optional. Tell us what applies and the plan above changes with it.</p>}
        <div className="flex flex-wrap gap-2" role="group" aria-label="Profile">
          {PROFILES.map((p) => <button key={p.id} className={`chip ${prefs.profile === p.id ? "on" : ""}`} aria-pressed={prefs.profile === p.id} title={p.hint} onClick={() => setPrefs({ ...prefs, profile: p.id, saved: true })}>{p.label}</button>)}
        </div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Health and activities">
          {TOGGLES.map((t) => <button key={t.id} className={`chip ${prefs.toggles.includes(t.id) ? "on" : ""}`} aria-pressed={prefs.toggles.includes(t.id)} title={t.hint} onClick={() => setPrefs({ ...prefs, toggles: flip(prefs.toggles, t.id), saved: true })}>{t.label}</button>)}
        </div>
        {prefs.toggles.includes("pollen_allergy") && (
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Pollen types">
            <span className="faint text-xs">Triggers:</span>
            {SPECIES.map((s) => <button key={s} className={`chip ${prefs.species.includes(s) ? "on" : ""}`} aria-pressed={prefs.species.includes(s)} onClick={() => setPrefs({ ...prefs, species: flip(prefs.species, s), saved: true })}>{s[0].toUpperCase() + s.slice(1)}</button>)}
          </div>
        )}
      </section>

      <Acc title="Alerts and notifications"><NotificationSettings alerts={alerts} /></Acc>

      {programmes.length > 0 && adv && (
        <Acc title="City programmes you may qualify for">
          <div className="grid gap-3">
            {programmes.map((c) => (
              <article key={c.id} className="grid gap-1">
                <h3 className="m-0 text-[15px] font-bold">{c.title}</h3>
                <p className="muted m-0 text-[13px]">{c.body}</p>
                {c.id === "zone_lookup" || c.id === "lez_check" ? <p className="faint m-0 text-xs">{adv.zone.zone === "inner" ? "Inside the inner zone" : adv.zone.zone === "outer" ? "Inside the outer zone" : "Outside the zones"} · illustrative until the municipality publishes official zones.</p> : null}
                {c.cta === "eligibility_wizard" && <Wizard district={adv.district} grantNote={adv.grant_note} />}
                {c.measure && <p className="faint m-0 text-[11px]">Sofia climate plan: {c.measure.label}</p>}
              </article>
            ))}
          </div>
        </Acc>
      )}
      <p className="faint m-0 text-xs">Indicative screening based on forecasts and citizen sensors, not medical advice. Heat uses the city forecast plus a learned local offset; air uses the city model.</p>
    </div>
  );
}
