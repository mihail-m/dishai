import { useEffect, useState } from "react";
import type { Advice, DistrictsFC, Profile, Scenario } from "../types";
import { LEVEL_TEXT, api } from "../api";
import Wizard from "./Wizard";

const PROFILES: { id: Profile; label: string; hint: string }[] = [
  { id: "driver", label: "Driver", hint: "I drive in the city" },
  { id: "wood_heater", label: "Heats with wood or coal", hint: "Stove or solid-fuel boiler" },
  { id: "vulnerable", label: "Vulnerable or older, asthma", hint: "Health sensitive" },
];

const measureColor: Record<string, string> = {
  PUGM: "#2a7ab5", "C.1.1": "#6a3fb5", "C.3.2": "#b5651d", "A.5.4": "#1f7a5c", "A.3.4": "#c0392f", "A.3.3": "#4a6a8a", "A.4.7": "#4a6a8a",
};

interface Props { scenario: Scenario; districts: DistrictsFC | null; district: string | null; setDistrict: (d: string) => void }

export default function ForYou({ scenario, districts, district, setDistrict }: Props) {
  const [profile, setProfile] = useState<Profile>("wood_heater");
  const [adv, setAdv] = useState<Advice | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const names = districts?.features.map((f) => f.properties.name).sort() ?? [];

  useEffect(() => {
    if (!district) return;
    let off = false;
    setErr(null);
    api.advice(profile, district, scenario).then((a) => !off && setAdv(a)).catch((e) => !off && setErr(String(e)));
    return () => { off = true; };
  }, [profile, district, scenario]);

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
      </div>

      {err && <div className="card p-4 text-sm" style={{ color: "var(--warn)" }}>Could not load advice: {err}</div>}
      {adv && (
        <>
          <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
            <div>
              <div className="text-lg font-semibold">{adv.district}: {LEVEL_TEXT[adv.status.level]} in the next 24 h</div>
              <div className="muted text-sm">{adv.status.alert.message} · district peak score {Math.round(adv.status.peak_score)}</div>
            </div>
            <span className={`badge pill-${adv.status.level}`}>{LEVEL_TEXT[adv.status.level]}</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {adv.cards.map((c) => (
              <article key={c.id} className="card flex flex-col p-4" style={c.severity === "action" ? { borderColor: "var(--accent)" } : undefined}>
                <span className="badge self-start" style={{ background: measureColor[c.measure.id] ?? "var(--accent)", borderColor: "transparent", color: "#fff" }}>
                  {c.measure.label}
                </span>
                <h3 className="mt-2 text-base font-semibold leading-snug">{c.title}</h3>
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
          <p className="faint text-xs">Each card cites the Sofia climate plan (PDUEK) measure it supports. Zone polygons are illustrative until the municipality publishes official geometry. The renovation grant is shown as "up to, per the plan" and may change.</p>
        </>
      )}
    </div>
  );
}
