import type { Forecast, Hour, Level } from "../types";
import { PM_WARNING, PM_WATCH, dayTime, hhmm } from "../api";

const SRC: Record<string, string> = {
  live: "Live data",
  cache: "Live data (cached up to 10 min)",
  stale: "Last known data: the live source is unreachable",
  fixture: "Offline: saved example data",
};
const SCENARIO_SRC: Record<string, string> = {
  winter_inversion: "Demo: simulated winter inversion",
  summer_heatwave: "Demo: simulated summer heatwave",
  birch_season: "Demo: simulated birch pollen season",
  serbia_wildfire: "Demo: simulated wildfire smoke from Serbia",
};

const WORDS: Record<Level, [string, string]> = {
  good: ["Good air in Sofia", "Fine for a walk or a run."],
  watch: ["Air is fair", "People with asthma, older people and small children should shorten time outdoors."],
  warning: ["Poor air", "Everyone: keep windows closed and skip outdoor exercise."],
};

const hourLevel = (v: number): Level => (v <= PM_WATCH ? "good" : v <= PM_WARNING ? "watch" : "warning");

/** One sentence about the next 24 hours, built from the alert. The stagnation score is a reason, never the headline. */
function outlook(fc: Forecast): string {
  const a = fc.alert;
  const peak = a.peak_pm25 != null && a.peak_pm25 > 0 ? `Peak ${Math.round(a.peak_pm25)} µg/m³${a.peak_time ? ` around ${hhmm(a.peak_time)}` : ""}.` : "";
  if (a.basis === "stagnation") return `No PM2.5 estimate right now. Air trapping score peaks at ${Math.round(a.peak_score)} of 100.`;
  if (a.active || (a.probability != null && a.level === "watch" && a.start)) return `${a.message}${peak ? ` ${peak}` : ""}`;
  if (a.level === "watch") return `Levels may pass the WHO daily guideline of ${PM_WATCH} µg/m³.`;
  return a.trapped ? `Air is trapped near the ground, but PM2.5 should stay low${peak ? `. ${peak}` : "."}` : "Stays good for the next 24 hours.";
}

export default function StatusCard({ fc, hour }: { fc: Forecast | null; hour: Hour | null | undefined }) {
  if (!fc || !hour) return <div className="status animate-pulse" style={{ background: "var(--surface-2)", minHeight: 150 }} aria-busy="true" />;
  const v = hour.pm25_model ?? hour.pm25;
  const level: Level = v != null ? hourLevel(v) : fc.alert.level;
  const [head, sub] = v != null ? WORDS[level] : ["Air quality forecast unavailable", "Showing the air-trapping forecast only."];
  const tone = level === "good" ? "" : level === "watch" ? "watch" : "warn";
  const isNow = hour.time === fc.hours[fc.now_index]?.time;
  const range = hour.pm25_p10 != null && hour.pm25_p90 != null && v != null ? `likely ${Math.round(hour.pm25_p10)} to ${Math.round(hour.pm25_p90)}` : null;
  const src = (fc.scenario && SCENARIO_SRC[fc.scenario]) || SRC[fc.source] || fc.source;
  const note = fc.alert.basis === "cams_bias" ? "Estimated from the regional model and live sensors."
    : fc.alert.scale === "sensor" ? "PM2.5 is on the citizen-sensor scale, which reads about half of reference stations." : null;
  return (
    <section className={`status ${tone}`} role="status" aria-live="polite">
      <div className="eyebrow">{isNow ? "Now" : dayTime(hour.time)}</div>
      <div className="flex items-start justify-between gap-3">
        <h2>{head}</h2>
        {v != null && <div className="pm"><b className="tabular-nums">{Math.round(v)}</b><span>µg/m³ PM2.5{range ? ` · ${range}` : ""}</span></div>}
      </div>
      <p className="muted m-0 text-sm">{sub}</p>
      <div className="outlook"><span aria-hidden="true">⏱</span><span>{outlook(fc)}</span></div>
      <div className={`text-[11px] ${fc.scenario || fc.source === "stale" ? "font-semibold" : ""}`} style={{ color: fc.scenario || fc.source === "stale" ? "var(--watch)" : "var(--ink-3)" }}>{src}{note ? ` · ${note}` : ""}</div>
    </section>
  );
}
