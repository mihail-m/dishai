import type { GoogleAir } from "../types";

const POLLEN_COLOR: Record<string, string> = { "Very Low": "var(--good)", Low: "var(--good)", Moderate: "var(--watch)", High: "var(--warn)", "Very High": "var(--warn)" };

function Spark({ v, now }: { v: number[]; now: number }) {
  const W = 240, H = 40, hi = Math.max(...v, 25), pts = v.map((y, i) => `${(i / Math.max(v.length - 1, 1)) * W},${H - 3 - (y / hi) * (H - 8)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 w-full" role="img" aria-label="Google PM2.5 forecast, next 48 hours">
      <line x1="0" x2={W} y1={H - 3 - (25 / hi) * (H - 8)} y2={H - 3 - (25 / hi) * (H - 8)} stroke="var(--ink-3)" strokeDasharray="3 3" opacity=".6" />
      <polyline points={pts} fill="none" stroke="var(--warn)" strokeWidth="2" />
      {now >= 0 && now < v.length && <circle cx={(now / Math.max(v.length - 1, 1)) * W} cy={H - 3 - (v[now] / hi) * (H - 8)} r="3.5" fill="var(--ink)" />}
    </svg>
  );
}

/** Google's independent PM2.5 / AQI / pollen for central Sofia, next to our own city-level estimate. */
export default function GoogleAirCard({ g, ours }: { g: GoogleAir | null; ours: number | null }) {
  if (!g || !g.enabled || !(g.current || g.pollen || g.forecast)) return null;
  const c = g.current, pm = c?.pollutants.pm25?.value ?? null;
  const f = (g.forecast ?? []).filter((h) => h.pm25 != null);
  const day = g.pollen?.[0];
  const types = day ? Object.entries(day.types).filter(([, t]) => t.value != null) : [];
  const idx = (code: string) => c?.indexes.find((i) => i.code === code);
  const ratio = pm != null && ours != null && ours > 0 ? pm / ours : null;
  return (
    <div className="card p-4">
      <div className="text-sm font-semibold">Google air quality &amp; pollen <span className="faint font-normal">· central Sofia, independent model</span></div>
      {c && (
        <>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <div><span className="text-3xl font-semibold tabular-nums">{pm != null ? Math.round(pm) : "–"}</span><span className="faint text-sm"> µg/m³ PM2.5 now</span></div>
            {ours != null && <div className="muted text-xs tabular-nums">our estimate {Math.round(ours)}{ratio ? ` · Google is ×${ratio.toFixed(1)}` : ""}</div>}
          </div>
          <p className="muted mt-1 text-xs">
            {idx("uaqi") && <>Universal AQI {idx("uaqi")!.aqi} ({idx("uaqi")!.category.toLowerCase()}). </>}
            {idx("bgr_niggg") && <>Bulgarian AQI {idx("bgr_niggg")!.aqi}/5 ({idx("bgr_niggg")!.category.toLowerCase()}). </>}
            {(["pm10", "no2", "o3"] as const).map((k) => c.pollutants[k]?.value != null ? `${c.pollutants[k].name} ${c.pollutants[k].value} ${c.pollutants[k].unit}` : null).filter(Boolean).join(" · ")}
          </p>
          {c.health.generalPopulation && <p className="mt-1.5 text-xs leading-snug">{c.health.generalPopulation}</p>}
        </>
      )}
      {f.length > 3 && (
        <div className="mt-2">
          <div className="faint text-[11px]">Google PM2.5 forecast, next {f.length} h (dashed line = EU 24 h limit, 25)</div>
          <Spark v={f.map((h) => h.pm25 as number)} now={-1} />
        </div>
      )}
      {types.length > 0 && day && (
        <div className="mt-2">
          <div className="text-xs font-semibold">Pollen today</div>
          <ul className="mt-1 grid gap-1 text-xs">
            {types.map(([k, t]) => (
              <li key={k} className="flex items-center justify-between">
                <span>{t.name}{!t.in_season && <span className="faint"> (out of season)</span>}</span>
                <span className="font-semibold" style={{ color: POLLEN_COLOR[t.category ?? ""] }}>{t.category} ({t.value}/5)</span>
              </li>
            ))}
          </ul>
          {day.plants.length > 0 && <p className="faint mt-1 text-[11px]">In season: {day.plants.map((p) => `${p.name} (${p.category.toLowerCase()})`).join(", ")}.</p>}
        </div>
      )}
      <p className="faint mt-2 text-[11px] leading-snug">
        Google Air Quality and Pollen APIs, ~500 m model, refreshed hourly. It does not use our citizen sensors, so it can differ a lot from the street-level estimate above: treat the gap as a reason to look at the municipal stations, not as proof either is right.
      </p>
    </div>
  );
}
