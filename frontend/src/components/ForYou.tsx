import { useEffect, useState } from "react";
import type { Advice, DistrictsFC, Profile, Scenario, Toggle } from "../types";
import { PM_LEVEL_TEXT, api } from "../api";
import Wizard from "./Wizard";

const PROFILES: { id: Profile; label: string; hint: string }[] = [
  { id: "driver", label: "Driver", hint: "I drive in the city" },
  { id: "wood_heater", label: "Heats with wood or coal", hint: "Stove or solid-fuel boiler" },
  { id: "vulnerable", label: "Vulnerable or older, asthma", hint: "Health sensitive" },
];

const TOGGLES: { id: Toggle; label: string; hint: string }[] = [
  { id: "pollen_allergy", label: "Pollen allergy", hint: "Adds pollen levels and timing" },
  { id: "asthma", label: "Asthma", hint: "Combines air, heat and pollen triggers" },
  { id: "heat_sensitive", label: "Sensitive to heat", hint: "Heart or health conditions, pregnancy, some medication" },
  { id: "outdoor_exercise", label: "Exercise outdoors", hint: "Finds the best slot for running or cycling" },
  { id: "kids_or_elderly", label: "Kids or older relatives", hint: "A plan for the people you look after" },
];
const STORE_KEY = "dishai.foryou";

function loadPrefs(): { profile?: Profile; toggles?: Toggle[] } {
  try {
    const p = JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}");
    return { profile: p.profile, toggles: Array.isArray(p.toggles) ? p.toggles.filter((t: string) => TOGGLES.some((x) => x.id === t)) : [] };
  } catch { return {}; }
}

const HEAT_TEXT = { none: "No heat stress", caution: "Warm", high: "Hot", extreme: "Extreme heat" } as const;
const POLLEN_TEXT = { none: "No pollen", low: "Low pollen", moderate: "Moderate pollen", high: "High pollen" } as const;
const levelPill = (l: string) => (l === "none" ? "" : l === "caution" || l === "low" ? "pill-watch" : "pill-warning");

const measureColor: Record<string, string> = {
  PUGM: "#2a7ab5", "C.1.1": "#6a3fb5", "C.3.2": "#b5651d", "A.5.4": "#1f7a5c", "A.3.4": "#c0392f", "A.3.3": "#4a6a8a", "A.4.7": "#4a6a8a",
};

interface Props { scenario: Scenario; districts: DistrictsFC | null; district: string | null; setDistrict: (d: string) => void }

export default function ForYou({ scenario, districts, district, setDistrict }: Props) {
  const [prefs] = useState(loadPrefs);
  const [profile, setProfile] = useState<Profile>(prefs.profile ?? "wood_heater");
  const [toggles, setToggles] = useState<Toggle[]>(prefs.toggles ?? []);
  const [adv, setAdv] = useState<Advice | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const names = districts?.features.map((f) => f.properties.name).sort() ?? [];

  useEffect(() => {
    if (!district) return;
    let off = false;
    setErr(null);
    api.advice(profile, district, scenario, toggles).then((a) => !off && setAdv(a)).catch((e) => !off && setErr(String(e)));
    return () => { off = true; };
  }, [profile, district, scenario, toggles]);

  useEffect(() => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ profile, toggles })); } catch { /* private mode: preferences just aren't remembered */ }
  }, [profile, toggles]);

  const flip = (t: Toggle) => setToggles((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));

  return (
    <div className="grid gap-4">
      <div className="card flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
        <div>
          <div className="faint mb-1 text-xs uppercase tracking-wide">I am a…</div>
          <div className="seg" role="group" aria-label="Profile">
            {PROFILES.map((p) => (
              <button key={p.id} aria-pressed={profile === p.id} title={p.hint} onClick={() => setProfile(p.id)}>{p.label}</button>
            ))}
          </div>
        </div>
        <label>
          <div className="faint mb-1 text-xs uppercase tracking-wide">My district</div>
          <select value={district ?? ""} onChange={(e) => setDistrict(e.target.value)} aria-label="District">
            {names.map((n) => <option key={n}>{n}</option>)}
          </select>
        </label>
        <div className="w-full">
          <div className="faint mb-1 text-xs uppercase tracking-wide">Also tailor for me <span className="normal-case">(saved on this device only)</span></div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Personal toggles">
            {TOGGLES.map((t) => (
              <button
                key={t.id}
                className="seg px-3 py-1.5 text-sm"
                style={{ background: toggles.includes(t.id) ? "var(--accent)" : undefined, color: toggles.includes(t.id) ? "#fff" : "var(--ink-2)", cursor: "pointer" }}
                aria-pressed={toggles.includes(t.id)}
                title={t.hint}
                onClick={() => flip(t.id)}
              >{t.label}</button>
            ))}
          </div>
        </div>
      </div>

      {err && <div className="card p-4 text-sm" style={{ color: "var(--warn)" }}>Could not load advice: {err}</div>}
      {adv && (
        <>
          <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
            <div>
              <div className="text-lg font-semibold">{adv.district}: {PM_LEVEL_TEXT[adv.status.level]} in the next 24 h</div>
              <div className="muted text-sm">{adv.status.alert.message}{adv.status.alert.trapped ? ` · air is trapped (stagnation score ${Math.round(adv.status.peak_score)})` : ""}</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`badge ${levelPill(adv.conditions.heat.level)}`} title={adv.conditions.heat.peak_c != null ? `Peak feels-like ${Math.round(adv.conditions.heat.peak_c)}°C at ${adv.conditions.heat.peak_hhmm}` : undefined}>
                {HEAT_TEXT[adv.conditions.heat.level]}{adv.conditions.heat.peak_c != null && adv.conditions.heat.level !== "none" ? ` · ${Math.round(adv.conditions.heat.peak_c)}°C` : ""}
              </span>
              <span className={`badge ${levelPill(adv.conditions.pollen.level)}`} title={adv.conditions.pollen.available ? adv.conditions.pollen.types.map((x) => `${x.label} ${x.grains}/m³`).join(", ") || "No measurable pollen" : "No pollen data"}>
                {adv.conditions.pollen.available ? POLLEN_TEXT[adv.conditions.pollen.level] : "Pollen: no data"}
              </span>
              <span className={`badge pill-${adv.status.level}`}>{PM_LEVEL_TEXT[adv.status.level]}</span>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {adv.cards.map((c) => (
              <article key={c.id} className="card flex flex-col p-4" style={c.severity === "action" ? { borderColor: "var(--accent)" } : undefined}>
                {c.measure && (
                  <span className="badge self-start" style={{ background: measureColor[c.measure.id] ?? "var(--accent)", borderColor: "transparent", color: "#fff" }}>
                    {c.measure.label}
                  </span>
                )}
                <h3 className={`${c.measure ? "mt-2 " : ""}text-base font-semibold leading-snug`}>{c.title}</h3>
                <p className="muted mt-1 text-sm">{c.body}</p>
                {c.id === "zone_lookup" && (
                  <div className="mt-3 rounded-lg p-2.5 text-sm font-semibold" style={{ background: "var(--surface-2)" }}>
                    {adv.zone.zone === "inner" ? "Inside the inner zone" : adv.zone.zone === "outer" ? "Inside the outer zone" : "Outside the zones"}
                    <span className="faint font-normal"> · illustrative</span>
                  </div>
                )}
                {c.cta === "eligibility_wizard" && <Wizard district={adv.district} grantNote={adv.grant_note} />}
              </article>
            ))}
          </div>
          <p className="faint text-xs">Each card cites the Sofia climate plan (PDUEK) measure it supports. Heat uses the city-wide feels-like forecast; pollen bands are approximate and the feed only covers the pollen season. Zone polygons are illustrative until the municipality publishes official geometry. The renovation grant is shown as "up to, per the plan" and may change.</p>
        </>
      )}
    </div>
  );
}
