import type { Alert, Forecast } from "../types";
import { PM_WARNING, PM_WATCH, hhmm } from "../api";

const SRC: Record<string, string> = {
  live: "Live data",
  cache: "Live data (cached ≤10 min)",
  stale: "Last known data (upstream unreachable)",
  fixture: "Offline: saved fixture",
  scenario: "Demo scenario: simulated winter inversion",
};
const SCENARIO_SRC: Record<string, string> = {
  winter_inversion: "Demo scenario: simulated winter inversion",
  summer_heatwave: "Demo scenario: simulated summer heatwave",
  birch_season: "Demo scenario: simulated birch pollen season",
};

/** One sentence under the headline: the level behind it, and the stagnation score as the reason, never as the trigger. */
function detail(a: Alert): string {
  const score = Math.round(a.peak_score);
  if (a.basis === "stagnation") return `Peak stagnation score ${score} of 100. No PM2.5 estimate is available right now.`;
  if (a.active) {
    return `Peak ${Math.round(a.peak_pm25 ?? 0)} µg/m³${a.peak_time ? ` around ${hhmm(a.peak_time)}` : ""}.`
      + (a.trapped ? ` The air is trapped near the ground (stagnation score ${score} of 100).` : "");
  }
  if (a.level === "watch") return `WHO 24 h guideline is ${PM_WATCH} µg/m³; the EU limit is ${PM_WARNING}.`;
  return a.trapped
    ? `The air is trapped (stagnation score ${score} of 100), but PM2.5 is expected to stay low${a.peak_pm25 != null ? `, up to ${Math.round(a.peak_pm25)} µg/m³` : ""}.`
    : "Air is mixing well over the next 24 hours.";
}

export default function Banner({ fc }: { fc: Forecast | null }) {
  if (!fc) return <div className="card h-[72px] animate-pulse" />;
  const a = fc.alert;
  const sim = fc.scenario !== null;
  const tone = a.active ? "warn" : a.level === "watch" ? "watch" : "good";
  const note = a.basis === "cams_bias" ? "Estimated from the regional model and live sensors; the learned model is not available."
    : a.scale === "sensor" ? "PM2.5 is on the citizen-sensor scale, which reads roughly half of reference stations; not yet calibrated." : null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] px-5 py-4"
      style={{ background: `var(--${tone}-bg)`, border: `1px solid var(--${tone})` }}
    >
      <div className="flex items-center gap-3">
        <span aria-hidden className="text-2xl">{a.active ? "⚠" : a.level === "watch" ? "●" : "✓"}</span>
        <div>
          <div className="text-lg font-semibold" style={{ color: `var(--${tone})` }}>{a.message}</div>
          <div className="muted text-sm">{detail(a)}</div>
          {note && <div className="faint text-xs">{note}</div>}
        </div>
      </div>
      <span className="badge" style={sim ? { background: "var(--watch-bg)", color: "var(--watch)" } : undefined}>
        {(fc.scenario && SCENARIO_SRC[fc.scenario]) || SRC[fc.source] || fc.source}
      </span>
    </div>
  );
}
