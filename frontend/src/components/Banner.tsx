import type { Forecast } from "../types";

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

export default function Banner({ fc }: { fc: Forecast | null }) {
  if (!fc) return <div className="card h-[72px] animate-pulse" />;
  const a = fc.alert;
  const sim = fc.scenario !== null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] px-5 py-4"
      style={{ background: a.active ? "var(--warn-bg)" : "var(--good-bg)", border: `1px solid ${a.active ? "var(--warn)" : "var(--good)"}` }}
    >
      <div className="flex items-center gap-3">
        <span aria-hidden className="text-2xl">{a.active ? "⚠" : "✓"}</span>
        <div>
          <div className="text-lg font-semibold" style={{ color: a.active ? "var(--warn)" : "var(--good)" }}>{a.message}</div>
          <div className="muted text-sm">
            {a.active
              ? `Peak stagnation score ${Math.round(a.peak_score)} of 100. Pollution is trapped near the ground while the air is calm under a warm lid.`
              : "Air is mixing well over the next 24 hours."}
          </div>
        </div>
      </div>
      <span className="badge" style={sim ? { background: "var(--watch-bg)", color: "var(--watch)" } : undefined}>
        {(fc.scenario && SCENARIO_SRC[fc.scenario]) || SRC[fc.source] || fc.source}
      </span>
    </div>
  );
}
