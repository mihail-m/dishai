import { useEffect, useMemo, useState } from "react";
import type { CalendarResp, EnvSeries, GoogleAir, PollenLevel, PollenResp, PollenSpecies, Scenario, SeasonStatus, Severity, Species, SymptomSummary } from "../types";
import { api, api2 } from "../api";
import { SPECIES_KIND, SPECIES_LABEL } from "../prefs";
import { CalendarHeat, DayBars, LEVEL_WORD, Sparkline, levelFill, levelInk } from "../components/PollenCharts";

const pill = (l: PollenLevel) => (l === "none" ? "" : l === "low" ? "pill-good" : l === "moderate" ? "pill-watch" : "pill-warning");
const ENV_PILL = (i: number, n: number) => (i === 0 ? "pill-good" : i < n - 1 ? "pill-watch" : "pill-warning");
const CHECKIN_KEY = "dishai.checkin";

function seasonLine(s: SeasonStatus | undefined): string {
  if (!s || s.status === "none") return "No stable season in the model history for Sofia.";
  if (s.status === "in_season") return `Usually in season until about ${s.end}${s.days_left != null ? ` (${s.days_left} days)` : ""}.`;
  if (s.status === "upcoming") return `Usually starts in about ${s.in_days} days, around ${s.start}.`;
  return `This year's season is usually over. Next one from about ${s.start}.`;
}

function SpeciesCard({ s, season, mine, today }: { s: PollenSpecies; season?: SeasonStatus; mine: boolean; today: string }) {
  const lvl = s.now?.level ?? "none";
  const peakDay = s.days.reduce((a, d) => ((d.peak ?? -1) > (a?.peak ?? -1) ? d : a), s.days[0]);
  const quiet = s.days.every((d) => !d.peak);
  return (
    <article className="card grid content-start gap-3 p-4" style={mine ? { borderColor: "var(--accent)" } : undefined}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="m-0 text-base font-bold">{s.label} <span className="faint text-xs font-medium">{SPECIES_KIND[s.type]}</span></h3>
          {mine && <span className="eyebrow" style={{ color: "var(--accent)" }}>Your trigger</span>}
        </div>
        <span className={`badge ${pill(lvl)}`}>{s.now ? `${LEVEL_WORD[lvl]} · ${s.now.grains}` : "no data"}</span>
      </div>
      {quiet ? <p className="muted m-0 text-sm">No measurable {s.label.toLowerCase()} pollen in the next days.</p> : (
        <>
          <DayBars days={s.days} high={s.bands.high} today={today} />
          <Sparkline s={s} />
          {peakDay?.peak ? <p className="muted m-0 text-xs">Highest {peakDay.peak} grains/m³ on {new Date(`${peakDay.date}T12:00:00`).toLocaleDateString("en-GB", { weekday: "long" })} around {peakDay.peak_hhmm}.</p> : null}
        </>
      )}
      <p className="faint m-0 text-xs">{seasonLine(season)}</p>
    </article>
  );
}

function EnvTable({ name, unit, s, hint }: { name: string; unit: string; s: EnvSeries; hint: string }) {
  if (!s.available) return <div className="card p-4"><div className="font-semibold">{name}</div><p className="faint m-0 mt-1 text-xs">No data in the feed right now.</p></div>;
  return (
    <div className="card p-4">
      <div className="font-semibold">{name}</div>
      <p className="faint m-0 mb-2 text-xs">{hint}</p>
      <table className="w-full text-xs">
        <tbody>
          {s.days.map((d) => (
            <tr key={d.date} className="border-t" style={{ borderColor: "var(--line)" }}>
              <td className="py-1.5">{new Date(`${d.date}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric" })}</td>
              <td className="tabular-nums">{d.peak == null ? "–" : `${d.peak}${unit ? ` ${unit}` : ""}`}</td>
              <td className="text-right">{d.level && <span className={`badge ${ENV_PILL(s.levels.indexOf(d.level), s.levels.length)}`}>{d.level}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GooglePlants({ g }: { g: GoogleAir | null }) {
  const day = g?.pollen?.[0];
  if (!g || !g.enabled) return <p className="muted m-0 text-sm">Plant-level detail (hazel, oak, ash, pine, cottonwood and what each cross-reacts with) comes from the Google Pollen API. Add <b>GOOGLE_ROUTES_SERVER_KEY</b> and enable the Pollen API for it (see the README) to show it here.</p>;
  if (!day) return <p className="muted m-0 text-sm">Google returned no pollen data for Sofia right now.</p>;
  const types = Object.entries(day.types);
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-2">
        {types.map(([code, t]) => <span key={code} className={`badge ${t.category && /high/i.test(t.category) ? "pill-warning" : t.category && /moderate/i.test(t.category) ? "pill-watch" : t.in_season ? "pill-good" : ""}`}>{t.name}: {t.in_season ? `${t.category ?? "–"}${t.value != null ? ` (${t.value}/5)` : ""}` : "out of season"}</span>)}
      </div>
      {day.plants.length === 0 ? <p className="muted m-0 text-sm">No plant is flowering in Google's model for today.</p> : (
        <div className="grid gap-2 md:grid-cols-2">
          {day.plants.map((p) => (
            <div key={p.name} className="rounded-xl p-3 text-sm" style={{ background: "var(--surface-2)" }}>
              <div className="flex items-center justify-between gap-2"><b>{p.name}</b><span className="badge">{p.category} · {p.value}/5</span></div>
              <div className="faint text-xs">{[p.type, p.family, p.season && `season ${p.season}`].filter(Boolean).join(" · ")}</div>
              {p.cross_reaction && <p className="muted m-0 mt-1 text-xs">Cross-reacts with: {p.cross_reaction}</p>}
            </div>
          ))}
        </div>
      )}
      {types.map(([code, t]) => t.in_season && t.advice ? <p key={code} className="muted m-0 text-xs"><b>{t.name}:</b> {t.advice}</p> : null)}
      <p className="faint m-0 text-[11px]">Google Pollen API, 1 km model, 5-day index (1 to 5). It is a separate model from the CAMS values above, so the two can disagree.</p>
    </div>
  );
}

const CHECKINS: [Severity, string][] = [["none", "No symptoms"], ["mild", "Mild"], ["bad", "Bad"]];

function CheckIn({ district, species, scenario }: { district: string; species: Species[]; scenario: Scenario }) {
  const today = new Date().toLocaleDateString("en-CA");
  const asked = (() => { try { return localStorage.getItem(CHECKIN_KEY) === today; } catch { return false; } })();
  const [done, setDone] = useState(asked);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sum, setSum] = useState<SymptomSummary | null>(null);
  useEffect(() => { api.symptomSummary().then(setSum).catch(() => setSum(null)); }, [done]);
  const send = (sev: Severity) => {
    setErr(null);
    api.symptom(district, sev, species, scenario).then((r) => {
      setMsg(r.stored ? "Thanks, recorded anonymously." : r.reason ?? "Thanks.");
      setDone(true);
      if (r.stored) { try { localStorage.setItem(CHECKIN_KEY, today); } catch { /* the prompt just shows again */ } }
    }).catch((e) => setErr(String(e)));
  };
  return (
    <div className="grid gap-3">
      {!done ? (
        <>
          <p className="muted m-0 text-sm">How are your eyes, nose and breathing today in {district}? One tap, once a day. Only the date, district, your answer and the model's pollen level are stored: no name, device or location.</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Symptoms today">
            {CHECKINS.map(([k, label]) => <button key={k} className="btn" onClick={() => send(k)}>{label}</button>)}
          </div>
        </>
      ) : <p className="m-0 text-sm font-semibold" role="status">{msg ?? "You have checked in today. Thank you."}</p>}
      {err && <p className="m-0 text-sm" style={{ color: "var(--warn)" }}>Could not send: {err}</p>}
      {sum && (
        <div>
          <div className="eyebrow mb-1">Do reports follow the model? (last {sum.days} days)</div>
          {sum.reports === 0 ? <p className="faint m-0 text-xs">No reports yet. Once enough people check in, this will show how often people report bad symptoms on low, moderate and high pollen days.</p> : (
            <table className="w-full text-xs">
              <thead><tr className="faint text-left"><th className="py-1 font-normal">Model pollen level</th><th className="font-normal">Reports</th><th className="font-normal">Bad symptoms</th></tr></thead>
              <tbody>
                {sum.levels.map((l) => (
                  <tr key={l.level} className="border-t" style={{ borderColor: "var(--line)" }}>
                    <td className="py-1.5"><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: levelFill(l.level), border: `1px solid ${levelInk(l.level)}` }} />{LEVEL_WORD[l.level]}</td>
                    <td className="tabular-nums">{l.n}</td>
                    <td className="tabular-nums">{l.enough ? `${Math.round((l.bad_share ?? 0) * 100)}%` : `needs ${sum.min_n - l.n} more reports`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="faint m-0 mt-1 text-[11px]">{sum.note}</p>
        </div>
      )}
    </div>
  );
}

/** Pollen and allergy section of My health. `mine` is the plants the person reacts to (empty = all). */
interface Props { scenario: Scenario; district: string | null; mine: Species[] }

export default function AllergyView({ scenario, district, mine }: Props) {
  const [data, setData] = useState<PollenResp | null>(null);
  const [cal, setCal] = useState<CalendarResp | null>(null);
  const [google, setGoogle] = useState<GoogleAir | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let off = false;
    setErr(null);
    api.pollen(scenario).then((d) => !off && setData(d)).catch((e) => !off && setErr(String(e)));
    return () => { off = true; };
  }, [scenario]);
  useEffect(() => {
    api.pollenCalendar().then(setCal).catch(() => setCal({ available: false }));
    api2.googleAir().then(setGoogle).catch(() => setGoogle(null));
  }, []);

  const ordered = useMemo(() => {
    const list = data?.pollen.species ?? [];
    return [...list].sort((a, b) => Number(mine.includes(b.type)) - Number(mine.includes(a.type)) || (b.now?.grains ?? 0) - (a.now?.grains ?? 0));
  }, [data, mine]);
  const seasonOf = (k: Species) => data?.season.find((s) => s.type === k);

  // headline: what matters for this person right now
  const focus = ordered.filter((s) => !mine.length || mine.includes(s.type));
  const active = focus.filter((s) => s.now && s.now.level !== "none").sort((a, b) => (b.now!.grains / b.bands.high) - (a.now!.grains / a.bands.high));
  const worst = active[0];
  const worstPeak = worst ? Math.max(...worst.days.map((x) => x.peak ?? 0)) : 0;
  const soon = (data?.season ?? []).filter((s) => (!mine.length || mine.includes(s.type)) && s.status === "upcoming").sort((a, b) => (a.in_days ?? 0) - (b.in_days ?? 0))[0];
  const live = data?.pollen.available;

  return (
    <div className="grid gap-4">
      <div>
        <h2 className="m-0 text-xl font-bold">Pollen and allergy</h2>
        <p className="muted m-0 mt-1">Forecast by plant, when each season usually runs, and the other things that set off hay fever and asthma. Modelled for all of Sofia, not counted by a sampler. {mine.length ? "Your plants come first." : "Switch on “Pollen allergy” above to put the plants you react to first."}</p>
      </div>

      <div className="card grid gap-3 p-4">
        {err && <div className="text-sm" style={{ color: "var(--warn)" }}>Could not load the pollen forecast: {err}</div>}
        {data && (
          <div>
            <div className="text-lg font-semibold">
              {worst ? `${LEVEL_WORD[worst.now!.level][0].toUpperCase()}${LEVEL_WORD[worst.now!.level].slice(1)} ${worst.label.toLowerCase()} pollen${active.length > 1 ? ` and ${active.length - 1} more` : ""} now`
                : live ? "No pollen in the forecast right now" : "No pollen data right now: out of season"}
            </div>
            <div className="muted text-sm">
              {worst ? `${worst.now!.grains} grains/m³ now${worstPeak > worst.now!.grains ? `; up to ${worstPeak} in the next days` : ""}.`
                : soon ? `${SPECIES_LABEL[soon.type]} season usually starts in about ${soon.in_days} days (around ${soon.start}).` : "The CAMS feed only reports during the pollen season."}
              {worst && soon ? ` ${SPECIES_LABEL[soon.type]} usually starts in about ${soon.in_days} days.` : ""}
            </div>
          </div>
        )}
      </div>

      {data && live && (
        <section aria-label="Pollen forecast by plant">
          <div className="grid items-start gap-4 md:grid-cols-2">
            {ordered.map((s) => <SpeciesCard key={s.type} s={s} season={seasonOf(s.type)} mine={mine.includes(s.type)} today={data.today} />)}
          </div>
        </section>
      )}
      {data && !live && (
        <div className="card p-4 text-sm"><b>The pollen feed is empty.</b> <span className="muted">Open-Meteo only publishes pollen in the European pollen season, so there is nothing to chart today. The season calendar below shows when each plant usually starts.</span></div>
      )}

      <section aria-label="Typical pollen season" className="card grid gap-2 p-4">
        <h3 className="m-0 text-base font-bold">When is each season?</h3>
        {cal ? <CalendarHeat cal={cal} highlight={mine} /> : <div className="h-24 animate-pulse rounded-lg" style={{ background: "var(--surface-2)" }} />}
      </section>

      {data && (
        <section aria-label="Other triggers" className="grid gap-4 sm:grid-cols-2">
          <EnvTable name="UV index" unit="" s={data.environment.uv} hint="Daily peak. WHO: protect from 3, high from 6, very high from 8." />
          <EnvTable name="Ozone" unit="µg/m³" s={data.environment.ozone} hint="Daily peak. Irritates airways; WHO guideline 100, EU information level 180." />
          <EnvTable name="Mineral dust" unit="µg/m³" s={data.environment.dust} hint="Often Saharan. Hand-set bands: notable from 50, heavy from 100." />
        </section>
      )}

      <section aria-label="Plant detail from Google" className="card grid gap-2 p-4">
        <h3 className="m-0 text-base font-bold">Plant detail</h3>
        <GooglePlants g={google} />
      </section>

      <section aria-label="Symptom check-in" className="card grid gap-2 p-4">
        <h3 className="m-0 text-base font-bold">How are you today?</h3>
        <CheckIn district={district ?? "Sredets"} species={mine} scenario={scenario} />
      </section>

      <p className="faint text-xs">{data?.attribution ?? "CAMS European air quality forecast via Open-Meteo."} Contains modified Copernicus Atmosphere Monitoring Service information. Bands are approximate and indicative; this is not medical advice. No open measured pollen counts for Sofia were found: a local sampler would be the way to check the model.</p>
    </div>
  );
}
