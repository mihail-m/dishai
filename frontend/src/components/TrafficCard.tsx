import type { IncidentsResp, TrafficResp } from "../types";
import { trafficColor, trafficWord } from "../map/layers";

const css = (r: number | null) => `rgb(${trafficColor(r).join(",")})`;

/** Typical travel-time index by hour (weekday and weekend, mean of the corridors) with the current hour marked. */
function Profile({ p, hour, weekend, share }: { p: { wk: number[]; we: number[] }; hour: number; weekend: boolean; share?: boolean }) {
  const W = 240, H = 44;
  const all = [...p.wk, ...p.we], lo = share ? 0 : Math.min(...all), span = share ? 1 : Math.max(Math.max(...all) - lo, 0.02);
  const yy = (v: number) => H - 3 - ((v - lo) / span) * (H - 8);
  const pts = (v: number[]) => v.map((y, h) => `${(h / 23) * W},${yy(y)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H + 12}`} className="mt-1 w-full" role="img" aria-label="Typical traffic by hour: weekday and weekend">
      <polyline points={pts(p.wk)} fill="none" stroke="var(--warn)" strokeWidth="2" />
      <polyline points={pts(p.we)} fill="none" stroke="var(--ink-3)" strokeWidth="2" strokeDasharray="4 3" />
      <line x1={(hour / 23) * W} x2={(hour / 23) * W} y1="0" y2={H} stroke="var(--ink)" strokeWidth="1" opacity=".5" />
      <circle cx={(hour / 23) * W} cy={yy((weekend ? p.we : p.wk)[hour])} r="3.5" fill="var(--ink)" />
      {[0, 6, 12, 18, 23].map((h) => <text key={h} x={(h / 23) * W} y={H + 10} fontSize="9" textAnchor="middle" fill="var(--ink-3)">{String(h).padStart(2, "0")}</text>)}
    </svg>
  );
}

interface Props { t: TrafficResp | null; inc: IncidentsResp | null; show: boolean; setShow: (b: boolean) => void; hour: number; weekend: boolean }

export default function TrafficCard({ t, inc, show, setShow, hour, weekend }: Props) {
  if (!t || !t.enabled) return null;
  const rows = [...t.corridors].sort((a, b) => (b.vs_typical ?? 0) - (a.vs_typical ?? 0));
  const closures = (inc?.features ?? []).filter((f) => f.properties.magnitude_code === 4 || f.properties.category_code === 8).length;
  return (
    <div className="grid gap-1">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">Real traffic <span className="faint font-normal">· live vs typical</span></div>
        <label className="flex cursor-pointer items-center gap-1.5 text-xs">
          <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> Show on map
        </label>
      </div>
      {!t.ready ? (
        <p className="muted mt-2 text-xs">Reading the arterial corridors for the first time (about a minute).</p>
      ) : (
        <>
          <p className="muted mt-1 text-xs">
            {t.corridors.length} arterial corridors, measured live and compared with Google&apos;s typical travel time for this hour and weekday. Observed data only: it is not an input to the PM2.5 estimate.
            {inc?.enabled && <> {inc.count ?? 0} active incidents{closures ? `, ${closures} closures` : ""}.</>}
          </p>
          <ul className="mt-2 grid gap-1.5">
            {rows.map((c) => (
              <li key={c.id} className="text-xs" title={`${c.length_km} km`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: css(c.vs_typical) }} /><span className="truncate">{c.name}</span></span>
                  <span className="tabular-nums shrink-0 font-semibold">{c.vs_typical != null ? `×${c.vs_typical.toFixed(2)}` : "–"}</span>
                </div>
                <div className="faint pl-4 text-[11px]">
                  {trafficWord(c.vs_typical)}
                  {c.google.jam_share ? ` · jam on ${Math.round(c.google.jam_share * 100)}% of route` : c.google.slow_share ? ` · slow on ${Math.round(c.google.slow_share * 100)}%` : ""}
                  {c.tomtom.tti ? ` · TomTom ×${c.tomtom.tti.toFixed(2)}` : ""}
                </div>
              </li>
            ))}
          </ul>
          {t.volume_profile ? (
            <div className="mt-3">
              <div className="flex justify-between text-xs"><span className="font-semibold">Vehicles on Sofia&apos;s ring road, by hour (counted)</span>
                <span className="faint"><span style={{ color: "var(--warn)" }}>━</span> weekday <span>┅</span> weekend</span></div>
              <Profile p={{ wk: t.volume_profile.weekday, we: t.volume_profile.weekend }} hour={hour} weekend={weekend} share />
              <p className="faint text-[11px] leading-snug">
                Share of the weekday peak, from real counts at {t.volume_profile.points.length} control points ({t.volume_profile.from.slice(0, 10)} to {t.volume_profile.to.slice(0, 10)}).
                Heavy vehicles: {Math.round(t.volume_profile.heavy_share.weekday * 100)}% on weekdays, {Math.round(t.volume_profile.heavy_share.weekend * 100)}% at weekends.
                Contains data of the Bulgarian road toll authority (BGTOLL), CC BY 4.0.
              </p>
            </div>
          ) : t.profile && (
            <div className="mt-3">
              <div className="flex justify-between text-xs"><span className="font-semibold">Typical travel time by hour (Google)</span>
                <span className="faint"><span style={{ color: "var(--warn)" }}>━</span> weekday <span>┅</span> weekend</span></div>
              <Profile p={t.profile} hour={hour} weekend={weekend} />
            </div>
          )}
        </>
      )}
      <p className="faint mt-2 text-[11px] leading-snug">
        Sources: {t.sources.join("; ")}; TomTom Traffic Incidents. Index = travel time over the corridor&apos;s quietest-hour travel time (1.0 = free flow). These are travel times, not vehicle counts.
        Arterials only; streets in between have no measurement and none is assumed. The PM2.5 estimate has no traffic term, because turning travel times into µg/m³ would need traffic history lined up with the sensor record.
        {t.updated && ` Updated ${t.updated.slice(11, 16)}.`}
      </p>
    </div>
  );
}
