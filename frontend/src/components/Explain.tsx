import type { EdgeExplain } from "../types";
import { caiCss } from "../map/cai";

const ROWS: { key: keyof EdgeExplain["components_ugm3"]; label: string; hint: string }[] = [
  { key: "background", label: "Regional background", hint: "CAMS + weather + live-sensor nowcast, for a typical street" },
  { key: "local_correction", label: "Local sensor correction", hint: "nearby citizen sensors vs the model" },
  { key: "traffic", label: "Traffic", hint: "road class within 150 m (learned effect vs a typical street)" },
  { key: "ventilation", label: "Terrain / trapped air", hint: "valley floor vs slope and surface heat (learned); physics fallback: stagnation amplifier" },
  { key: "greenery", label: "Greenery shield", hint: "trees and NDVI within 50 m (capped at −30 %)" },
];
const colorName = (cai: number) => (cai < 25 ? "red" : cai < 45 ? "orange" : cai < 65 ? "amber" : cai < 85 ? "light green" : "green");

export default function Explain({ e, onClose }: { e: EdgeExplain | null; onClose: () => void }) {
  if (!e) {
    return (
      <div className="card p-4">
        <div className="text-sm font-semibold">Why is this red?</div>
        <p className="muted mt-1 text-sm">Click any street on the map to see what drives its Clean Air Index: background, traffic, trapped air, greenery and heat.</p>
      </div>
    );
  }
  const max = Math.max(1, ...ROWS.map((r) => Math.abs(e.components_ugm3[r.key])));
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="faint text-xs uppercase tracking-wide">Why is this {colorName(e.cai)}?</div>
          <div className="mt-0.5 font-semibold leading-tight">
            {e.street ? `${e.near_street ? "Footpath near " : ""}${e.street}` : `Unnamed ${e.road_class}`}
          </div>
          {e.street_bg && <div className="faint text-xs">{e.street_bg}</div>}
          <div className="faint text-xs">{e.district ?? "outside the city districts"} · {e.road_class} · {Math.round(e.length_m)} m segment (outlined on the map)</div>
        </div>
        <button className="btn !px-2 !py-0.5 text-xs" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="mt-3 flex items-baseline gap-4">
        <div><span className="text-4xl font-semibold tabular-nums" style={{ color: caiCss(e.cai) }}>{e.cai}</span><span className="faint text-sm"> CAI</span></div>
        <div className="muted text-sm tabular-nums">{e.pm25} µg/m³ PM2.5</div>
      </div>
      <ul className="mt-3 grid gap-2">
        {ROWS.map((r) => {
          const v = e.components_ugm3[r.key];
          return (
            <li key={r.key} title={r.hint}>
              <div className="flex justify-between text-xs"><span>{r.label}</span><span className="tabular-nums font-semibold">{v > 0 ? "+" : ""}{v.toFixed(1)}</span></div>
              <div className="relative mt-0.5 h-2 rounded" style={{ background: "var(--surface-2)" }}>
                <i className="absolute top-0 h-2 rounded" style={{
                  left: v >= 0 ? "50%" : `${50 - (Math.abs(v) / max) * 50}%`, width: `${(Math.abs(v) / max) * 50}%`,
                  background: v >= 0 ? "var(--warn)" : "var(--good)",
                }} />
                <i className="absolute left-1/2 top-[-2px] h-3 w-px" style={{ background: "var(--ink-3)" }} />
              </div>
            </li>
          );
        })}
        <li className="flex justify-between text-xs">
          <span title="Landsat surface-temperature anomaly, only on hot sunny hours">Heat</span>
          <span className="tabular-nums font-semibold">{e.drivers.heat_weight === 0 ? "off (not hot)" : `${e.heat_points > 0 ? "+" : ""}${e.heat_points} CAI points`}</span>
        </li>
      </ul>
      {(e.drivers.construction_share ?? 0) >= 0.02 && (
        <p className="muted mt-2 text-xs" title="OpenStreetMap landuse/building=construction within about 150 m">
          Construction nearby: {Math.round((e.drivers.construction_share ?? 0) * 100)}% of the area within 150 m. Shown for context, not in the score: citizen sensors near sites do not read dirtier.
        </p>
      )}
      <p className="faint mt-3 text-[11px] leading-snug">
        µg/m³ added to or removed from the street&apos;s PM2.5. Traffic term {e.drivers.traffic_term}, rush ×{e.drivers.rush}, trapping ×{e.drivers.amplifier}, greenery {e.drivers.greenery}.
        Indicative screening model with assumed weights.
      </p>
    </div>
  );
}
