import type { No2Resp } from "../types";

const pct = (e: number) => Math.round((2 ** e - 1) * 100);
const norm = (v: number[]) => { const m = Math.max(...v); return v.map((x) => x / m); };

/** Weekday NO2 and measured traffic over the day, each scaled to its own peak, so the shapes can be compared. */
function Diurnal({ no2, traffic }: { no2: number[]; traffic: number[] }) {
  const W = 240, H = 46, y = (v: number) => H - 3 - v * (H - 8), pts = (v: number[]) => norm(v).map((x, h) => `${(h / 23) * W},${y(x)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H + 12}`} className="mt-1 w-full" role="img" aria-label="Weekday NO2 and traffic by hour">
      <polyline points={pts(traffic)} fill="none" stroke="var(--ink-3)" strokeWidth="2" strokeDasharray="4 3" />
      <polyline points={pts(no2)} fill="none" stroke="var(--warn)" strokeWidth="2" />
      {[0, 6, 12, 18, 23].map((h) => <text key={h} x={(h / 23) * W} y={H + 10} fontSize="9" textAnchor={h === 0 ? "start" : h === 23 ? "end" : "middle"} fill="var(--ink-3)">{String(h).padStart(2, "0")}</text>)}
    </svg>
  );
}

function Forecast({ f }: { f: NonNullable<No2Resp["forecast"]> }) {
  const W = 240, H = 46, hi = Math.max(...f.map((p) => p.p90), 30), y = (v: number) => H - 3 - (v / hi) * (H - 8), x = (i: number) => (i / Math.max(f.length - 1, 1)) * W;
  const band = [...f.map((p, i) => `${x(i)},${y(p.p90)}`), ...f.map((p, i) => `${x(f.length - 1 - i)},${y(f[f.length - 1 - i].p10)}`)].join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H + 12}`} className="mt-1 w-full" role="img" aria-label="NO2 forecast with range">
      <polygon points={band} fill="var(--warn)" opacity=".15" />
      <polyline points={f.map((p, i) => `${x(i)},${y(p.no2)}`).join(" ")} fill="none" stroke="var(--warn)" strokeWidth="2" />
      <line x1="0" x2={W} y1={y(25)} y2={y(25)} stroke="var(--ink-3)" strokeDasharray="3 3" opacity=".6" />
      {[0, 12, 24, 36, 47].filter((i) => i < f.length).map((i) => <text key={i} x={x(i)} y={H + 10} fontSize="9" textAnchor={i === 0 ? "start" : i === f.length - 1 ? "end" : "middle"} fill="var(--ink-3)">{f[i].time.slice(11, 13)}h</text>)}
    </svg>
  );
}

/** Traffic and NO2: the correlation we can show with real counts, and the NO2 forecast it feeds. PM2.5 shows no traffic signal. */
export default function No2Card({ d }: { d: No2Resp | null }) {
  if (!d || !d.enabled || !d.study || !d.elasticity || !d.forecast?.length) return null;
  const s = d.study, e = d.elasticity, skill = d.eval?.["weather + CAMS NO2 + typical traffic (served)"] as { skill_pct: number } | undefined;
  const mae = d.eval?.median_abs_error_ugm3 as number | undefined, maeC = d.eval?.median_abs_error_constant_ugm3 as number | undefined;
  return (
    <div className="card p-4">
      <div className="text-sm font-semibold">Traffic &amp; NO₂ <span className="faint font-normal">· real vehicle counts vs measured air</span></div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-4">
        <div><span className="text-3xl font-semibold tabular-nums">{d.mean_24h != null ? Math.round(d.mean_24h) : "–"}</span><span className="faint text-sm"> µg/m³ NO₂, next 24 h mean</span></div>
        <div className="muted text-xs">WHO 24 h guideline {d.who_24h}</div>
      </div>
      <Forecast f={d.forecast} />
      <div className="faint text-[11px]">City mean over 48 h with the 10–90 % range; dashed = {d.who_24h} µg/m³ (a 24 h guideline, shown for scale).
        {mae != null && maeC != null && <> Typical error {mae} µg/m³ against {maeC} for guessing the average{skill ? `; ${Math.round(skill.skill_pct)} % better than a constant` : ""}.</>}</div>

      <div className="mt-3 text-xs font-semibold">What real traffic does to the air</div>
      <ul className="mt-1 grid gap-1 text-xs">
        <li><b>NO₂ follows traffic.</b> Weekdays vs weekends: NO₂ ×{s.weekday_weekend_ratio.no2}, traffic ×{s.weekday_weekend_ratio.traffic}. With weather and hour of day controlled,
          doubling traffic goes with <b>+{pct(e.mean)} % NO₂</b> (90 % range +{pct(e.lo)} to +{pct(e.hi)} %), and the range is above zero at {s.stations.filter((x) => x.lo > 0).length} of {s.stations.length} stations.</li>
        <li><b>PM₂.₅ and PM₁₀ do not.</b> Their traffic effect is +{pct(s.pm.pm25.elasticity)} % ({pct(s.pm.pm25.lo)} to +{pct(s.pm.pm25.hi)} %) and +{pct(s.pm.pm10.elasticity)} % ({pct(s.pm.pm10.lo)} to +{pct(s.pm.pm10.hi)} %): zero is inside both ranges.
          That is why the PM₂.₅ map has no traffic term.</li>
      </ul>
      <div className="mt-2 flex justify-between text-xs"><span className="font-semibold">Weekday: NO₂ and traffic over the day</span>
        <span className="faint"><span style={{ color: "var(--warn)" }}>━</span> NO₂ <span>┅</span> vehicles</span></div>
      {d.traffic_profile && <Diurnal no2={s.diurnal_no2.weekday} traffic={d.traffic_profile.weekday} />}

      {d.scenarios && (
        <div className="mt-2 rounded-lg p-2.5 text-xs" style={{ background: "var(--surface-2)" }}>
          <div className="font-semibold">If traffic fell across the city</div>
          <ul className="mt-0.5">
            {d.scenarios.map((x) => <li key={x.traffic_cut_pct}>{x.traffic_cut_pct} % less traffic: NO₂ {x.no2_change_pct} % ({x.range_pct[0]} to {x.range_pct[1]} %)</li>)}
          </ul>
          <div className="faint mt-1 text-[11px]">City-wide NO₂ is mostly background, so traffic cuts alone move the average little; streets right next to busy roads can differ.</div>
        </div>
      )}
      <details className="mt-2 text-[11px]">
        <summary className="faint cursor-pointer">How this was measured, and what it cannot say</summary>
        <ul className="faint mt-1 grid gap-1 leading-snug">
          <li>Traffic: BGTOLL open data (CC BY 4.0), vehicle counts at {s.stations.length ? "12" : ""} control points on the ring road and approaches. NO₂, PM: EEA e-reporting stations (CC BY 4.0), {d.n_hours} hours, {d.range?.[0].slice(0, 10)} to {d.range?.[1].slice(0, 10)}.</li>
          {(d.caveats ?? []).map((c) => <li key={c}>{c}</li>)}
        </ul>
      </details>
    </div>
  );
}
