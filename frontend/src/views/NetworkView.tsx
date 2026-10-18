import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { AreaPlace, AreasResp, Chokepoint, NetworkResp, Scenario } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { caiCss, relCss } from "../map/cai";
import { areaRows, chokepointLayers } from "../map/layers";
import { hazeLayer } from "../map/haze";
import { AreaSurface, areaField, areaPickLayer, prefersReducedMotion, pulseLayers, type Animated } from "../map/surface";
import { Acc } from "../components/ui";

interface Props { stage: Stage; scenario: Scenario }

export const MEASURE_COLOR: Record<string, string> = { "A.4.3": "#2a7ab5", "A.4.7": "#4a6a8a", "C.1.1": "#6a3fb5", "A.4.4": "#1f7a5c" };
const TIP = { backgroundColor: "#111827", color: "#fff", fontSize: "12px", borderRadius: "8px", padding: "8px 10px", lineHeight: "1.4" };

/** Explore, "Clean paths": air quality areas, a deep-green haze where the cleanest routes run, and the busy streets with poor air. Nothing to configure. */
export default function NetworkView({ stage, scenario }: Props) {
  const [data, setData] = useState<{ key: string; net: NetworkResp; areas: AreasResp } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<Chokepoint | null>(null);
  const { set } = stage;
  const window = stage.forecast;
  const key = window ? `${window.id}:${scenario}` : null;

  // the clean paths and busy streets describe the city as it is now: one request, not one per forecast hour
  useEffect(() => {
    if (!window || !key) return;
    const controller = new AbortController();
    const opts = { forecastId: window.id, signal: controller.signal };
    setErr(null);
    Promise.all([api2.network(scenario, undefined, "walk", opts), api2.areas(scenario, undefined, "walk", "air", opts)])
      .then(([net, areas]) => { if (!controller.signal.aborted) setData({ key, net, areas }); })
      .catch((e) => { if (!controller.signal.aborted) setErr(String(e?.message ?? e)); });
    return () => controller.abort();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const net = data?.key === key ? data.net : null;
  const areas = data?.key === key ? data.areas : null;
  useEffect(() => { setSel((old) => (old ? net?.chokepoints.find((c) => c.street === old.street) ?? null : null)); }, [net]);

  const rows = useMemo(() => (areas ? areaRows(areas) : []), [areas]);
  const surface = useRef(new AreaSurface()).current;
  const pings = useRef<AreaPlace[]>([]);
  useEffect(() => { surface.set(areas ? areaField(areas, "relative", rows) : null); pings.current = []; }, [surface, areas, rows]);
  const animated = useCallback<Animated>((now) => {
    const s = surface.frame(now);
    return { under: s.layers, over: pulseLayers(pings.current, now, [201, 96, 76]), settled: s.settled };
  }, [surface]);

  const layers = useMemo(() => {
    const out = [];
    if (areas) out.push(areaPickLayer(areas, rows));
    if (net) {
      const haze = hazeLayer(net);
      if (haze) out.push(haze);
      out.push(...chokepointLayers(net, sel));
    }
    return out;
  }, [areas, rows, net, sel]);

  const tooltip = useMemo(() => (info: PickingInfo) => {
    const o = info.object as never as Record<string, number> | undefined;
    if (!o || info.layer?.id !== "areas" || !areas) return null;
    return { html: `Air quality score <b>${Math.round(o.mean)}</b><br/>PM2.5 ≈ ${o.pm25} µg/m³<br/>${(o.street_m / 1000).toFixed(1)} km of streets nearby`, style: TIP };
  }, [areas]);

  const legend = useMemo(() => ({
    kind: "clean" as const, title: "Clean paths",
    bar: (
      <>
        <div className="ramp mb-1 mt-2" style={{ background: `linear-gradient(90deg, ${[0, 25, 50, 75, 100].map(relCss).join(",")})` }} />
        <div className="faint flex justify-between text-[11px]"><span>dirtier</span><span>cleaner</span></div>
      </>
    ),
  }), []);

  useEffect(() => {
    set({ layers, base: "off", cursor: undefined, tooltip, legend, controls: undefined, animated, onPick: (info) => { if (info.layer?.id === "net-choke-dot" && info.object) setSel(info.object as Chokepoint); } });
  }, [layers, tooltip, legend, animated, set]);
  useEffect(() => () => set({ layers: [], base: "off" }), [set]);

  const choose = (c: Chokepoint) => { setSel(c); stage.map.current?.flyTo(c.lon, c.lat, 15); };

  return (
    <div className="grid gap-4">
      <p className="muted m-0 text-[13px]">The dark green haze is where the cleanest walking routes keep going. Numbered pins are busy streets with poor air.</p>
      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {!net && !err && <div className="card h-32 animate-pulse" />}
      {net && (
        <div>
          <h3 className="eyebrow mb-1">Busy streets with poor air</h3>
          <ol className="m-0 grid list-none gap-1 p-0">
            {net.chokepoints.map((c) => (
              <li key={c.rank}>
                <button className="row-btn flex-wrap" aria-pressed={sel?.rank === c.rank} style={{ border: "1px solid var(--line)", background: sel?.rank === c.rank ? "var(--surface-2)" : undefined }} onClick={() => choose(c)}>
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-bold text-white" style={{ background: "#d6288c" }}>{c.rank}</span>
                  <span className="min-w-0 flex-1 truncate font-semibold">{c.street}</span>
                  <span className="flex items-center gap-1.5 text-sm font-semibold tabular-nums"><i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: caiCss(c.mean_cai) }} />{Math.round(c.mean_cai)}</span>
                  <span className="faint w-full pl-9 text-[11px]">{c.street_bg} · {c.district} · {c.road_class}</span>
                  {sel?.rank === c.rank && (
                    <ul className="m-0 grid w-full list-none gap-1.5 p-0 pl-9">
                      {c.measures.map((m) => (
                        <li key={m.id} className="text-xs leading-snug">
                          <span className="badge mr-1.5" style={{ background: MEASURE_COLOR[m.id], color: "#fff", borderColor: "transparent" }}>{m.id} {m.label}</span>
                          <span className="muted">{m.why}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
      <Acc title="About this layer">
        <p className="faint m-0 text-[11px] leading-snug">Walking, 4.8 km/h. The haze comes from 400 seeded random trips and shows where the cleanest routes concentrate, not individual streets. Measures are suggestions mapped to the Sofia climate plan (PDUEK), not engineering assessments. {areas?.note}</p>
      </Acc>
    </div>
  );
}
