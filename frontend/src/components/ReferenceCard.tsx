import type { ReferenceResp } from "../types";

export default function ReferenceCard({ ref_ }: { ref_: ReferenceResp | null }) {
  if (!ref_) return null;
  if (!ref_.enabled) {
    // no token configured: nothing to show to a visitor; demo mode: say why the real stations are hidden
    return ref_.reason?.startsWith("Demo") ? <p className="faint px-1 text-[11px]">Municipal reference stations are real data, so they are hidden in the demo scenario.</p> : null;
  }
  const sc = ref_.sensor_check;
  return (
    <div className="card p-4">
      <div className="text-sm font-semibold">Municipal reference stations</div>
      <p className="faint mt-0.5 text-xs">Ringed dots on the map. Calibrated instruments, hourly.</p>
      <table className="mt-2 w-full text-xs">
        <thead><tr className="faint text-left"><th className="font-normal">Station</th><th className="font-normal">PM10 µg/m³</th><th className="font-normal">NO₂ AQI</th><th className="font-normal">O₃ AQI</th></tr></thead>
        <tbody>
          {ref_.stations.map((s) => (
            <tr key={s.uid} className="border-t" style={{ borderColor: "var(--line)" }}>
              <td className="py-1 font-semibold" title={s.name_bg ?? undefined}>{s.name}</td>
              <td className="tabular-nums">{s.pm10 != null ? Math.round(s.pm10) : "–"}</td>
              <td className="tabular-nums">{s.no2_aqi != null ? Math.round(s.no2_aqi) : "–"}</td>
              <td className="tabular-nums">{s.o3_aqi != null ? Math.round(s.o3_aqi) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {sc && sc.median_ratio != null && (
        <div className="mt-3 rounded-lg p-2.5 text-xs" style={{ background: "var(--surface-2)" }}>
          <div className="font-semibold">Citizen sensors vs these stations (PM10)</div>
          <div className="mt-0.5">{sc.verdict}: ratio {sc.median_ratio} across {sc.rows.length} stations
            ({sc.rows.map((r) => `${r.station} ${r.ratio}`).join(", ")}), using sensors within {sc.radius_m / 1000} km.</div>
          <div className="faint mt-1 text-[11px]">Indicative: one hour, PM10 only. The stations do not report PM2.5, so this does not prove the same for the PM2.5 values the model uses, but it is a reason to treat citizen-sensor levels with care.</div>
        </div>
      )}
      <p className="faint mt-2 text-[11px] leading-snug">{ref_.attribution}. PM10 is converted from the published AQI, so it is approximate. {ref_.note}</p>
    </div>
  );
}
