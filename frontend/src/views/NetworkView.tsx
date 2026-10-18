import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { AreaPlace, AreasResp, Chokepoint, Mode, NetworkResp, Scenario } from "../types";
import type { Stage } from "../stage";
import { useHourlyPreload } from "../forecast";
import { api2 } from "../api";
import { caiCss, relCss } from "../map/cai";
import { areaRows, chokepointLayers, flowLayer, heatColor, segmentLayers } from "../map/layers";
import { AreaSurface, areaField, areaPickLayer, prefersReducedMotion, pulseLayers, type Animated } from "../map/surface";
import AreaLists from "../components/AreaLists";
import { Acc, McBtn, McGroup } from "../components/ui";

interface Props { stage: Stage; scenario: Scenario; hourIndex: number; layer: "heat" | "clean" }

export const MEASURE_COLOR: Record<string, string> = { "A.4.3": "#2a7ab5", "A.4.7": "#4a6a8a", "C.1.1": "#6a3fb5", "A.4.4": "#1f7a5c" };
const TIP = { backgroundColor: "#111827", color: "#fff", fontSize: "12px", borderRadius: "8px", padding: "8px 10px", lineHeight: "1.4" };
const rgb = (c: number[]) => `rgb(${c.join(",")})`;

/** Explore, "Heat" and "Clean paths" layers: surface temperature areas, or the clean channels and busy streets with poor air. */
export default function NetworkView({ stage, scenario, hourIndex, layer }: Props) {
  const [mode, setMode] = useState<Mode>("walk");
  const [heatAreas, setHeatAreas] = useState<{ key: string; value: AreasResp } | null>(null);
  const [sel, setSel] = useState<Chokepoint | null>(null);
  const [showAreas, setShowAreas] = useState(true);
  const [showChannels, setShowChannels] = useState(true);
  const [showChoke, setShowChoke] = useState(true);
  const [showOther, setShowOther] = useState(false);
  const [flow, setFlow] = useState(false);
  const [scale, setScale] = useState<"relative" | "absolute">("relative");
  const [t, setT] = useState(0);
  const { set } = stage;
  const heat = layer === "heat";

  const window = stage.forecast;
  const key = window ? `${window.id}:network:${mode}` : null;
  const history = useHourlyPreload<{ net: NetworkResp; areas: AreasResp }>(key, window?.hours ?? [], hourIndex,
    async (hour, signal) => {
      const opts = { forecastId: window!.id, signal };
      const [net, areas] = await Promise.all([api2.network(scenario, hour, mode, opts), api2.areas(scenario, hour, mode, "air", opts)]);
      return { net, areas };
    });
  const net = history.value?.net ?? null;
  const areas = heat ? (heatAreas?.key === key ? heatAreas.value : null) : history.value?.areas ?? null;
  const err = history.status.error;
  const { reportPreload } = stage;
  useEffect(() => { reportPreload("network", history.status); }, [reportPreload, key, history.status.loaded, history.status.error]);
  useEffect(() => {
    setSel((old) => old ? net?.chokepoints.find((c) => c.street === old.street) ?? null : null);
  }, [net]);
  useEffect(() => {
    if (!window || !key) return;
    const controller = new AbortController();
    api2.areas(scenario, undefined, mode, "heat", { forecastId: window.id, signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setHeatAreas({ key, value }); }).catch(() => {});
    return () => controller.abort();
  }, [key]);

  useEffect(() => {
    if (!flow || !net || heat) return;
    let raf = 0, last = 0;
    const tick = (now: number) => { if (now - last > 40) { last = now; setT(now / 6000); } raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [flow, net, heat]);

  const rows = useMemo(() => (areas ? areaRows(areas) : []), [areas]);
  const surface = useRef(new AreaSurface()).current;
  const pings = useRef<AreaPlace[]>([]);
  useEffect(() => {
    if (!showAreas) { surface.set(null); pings.current = []; }
    else if (areas) { surface.set(areaField(areas, scale, rows)); pings.current = heat ? areas.extremes.worst : []; } // hottest spots; the clean-paths layer has its own markers
  }, [surface, showAreas, areas, scale, rows, heat]);
  const animated = useCallback<Animated>((now) => {
    const s = surface.frame(now);
    return { under: s.layers, over: pulseLayers(pings.current, now, [201, 96, 76]), settled: s.settled && (!pings.current.length || prefersReducedMotion()) };
  }, [surface]);
  const layers = useMemo(() => {
    const out = [];
    if (showAreas && areas) out.push(areaPickLayer(areas, rows));
    if (net && !heat) {
      out.push(...segmentLayers(net, { channels: showChannels, other: showOther }));
      if (showChoke) out.push(...chokepointLayers(net, sel));
      if (flow) out.push(flowLayer(net, t));
    }
    return out;
  }, [areas, rows, scale, net, heat, showAreas, showChannels, showChoke, showOther, sel, flow, t]);

  const tooltip = useMemo(() => (info: PickingInfo) => {
    const o = info.object as never as Record<string, number> | undefined;
    if (!o) return null;
    if (info.layer?.id === "areas" && areas) {
      const km = (o.street_m / 1000).toFixed(1);
      const html = areas.metric === "heat"
        ? `<b>Surface ${o.mean} °C</b><br/>${o.anomaly > 0 ? "+" : ""}${o.anomaly} °C vs city median (${areas.reference} °C)<br/>${km} km of streets · summer snapshot`
        : `<b>Area of ~${areas.cell_m} m</b><br/>Air quality score <b>${Math.round(o.mean)}</b> (cleanest street ${Math.round(o.max)}, worst ${Math.round(o.min)})<br/>PM2.5 ≈ ${o.pm25} µg/m³<br/>${km} km of streets`;
      return { html, style: TIP };
    }
    if (info.layer?.id === "net-channel") return { html: `<b>Clean path</b><br/>Score ${o.cai} · used by ${o.usage_clean} of 400 cleanest routes`, style: TIP };
    return null;
  }, [areas]);

  const legend = useMemo(() => {
    const range = areas ? Math.max(Math.abs(areas.thresholds.cool ?? 0), Math.abs(areas.thresholds.hot ?? 0), 1) : 5;
    const bar = heat
      ? `linear-gradient(90deg, ${[-1, -0.5, 0, 0.5, 1].map((u) => rgb(heatColor(u * range, range))).join(",")})`
      : `linear-gradient(90deg, ${[0, 25, 50, 75, 100].map((x) => (scale === "relative" ? relCss(x) : caiCss(x))).join(",")})`;
    return {
      kind: layer, title: heat ? "Surface temperature" : "Clean paths",
      bar: (
        <>
          <div className="ramp mb-1 mt-2" style={{ background: bar }} />
          <div className="faint flex justify-between text-[11px]">{heat ? <><span>cooler</span><span>hotter</span></> : <><span>dirtier</span><span>cleaner</span></>}</div>
        </>
      ),
    };
  }, [areas, heat, layer, scale]);

  const controls = useMemo(() => (heat ? undefined : (
    <>
      <McGroup label="Map layers">
        <McBtn on={showAreas} onClick={() => setShowAreas((x) => !x)}>Areas</McBtn>
        <McBtn on={showChannels} onClick={() => setShowChannels((x) => !x)} label="Clean channels">Clean paths</McBtn>
        <McBtn on={showChoke} onClick={() => setShowChoke((x) => !x)} label="Busy streets with poor air">Busy streets</McBtn>
        <McBtn on={showOther} onClick={() => setShowOther((x) => !x)} label="Other busy streets">Other streets</McBtn>
        <McBtn on={flow} onClick={() => setFlow((x) => !x)} label="Animate route flow">Animate</McBtn>
      </McGroup>
      <McGroup label="Area colour scale">
        <McBtn on={scale === "relative"} onClick={() => setScale("relative")} title="Stretch the 5th-95th percentile of areas to the full colour range">Relative</McBtn>
        <McBtn on={scale === "absolute"} onClick={() => setScale("absolute")} title="Absolute air quality score">Absolute</McBtn>
      </McGroup>
    </>
  )), [heat, showAreas, showChannels, showChoke, showOther, flow, scale]);

  useEffect(() => {
    set({
      layers, base: "off", cursor: undefined, tooltip, legend, controls, animated,
      onPick: (info) => { if (info.layer?.id === "net-choke-dot" && info.object) setSel(info.object as Chokepoint); },
    });
  }, [layers, tooltip, legend, controls, animated, set]);
  useEffect(() => () => set({ layers: [], base: "off" }), [set]);

  const fly = (lon: number, lat: number) => stage.map.current?.flyTo(lon, lat, 15);
  const choose = (c: Chokepoint) => { setSel(c); fly(c.lon, c.lat); };
  const ex = areas?.extremes;
  const modeWord = mode === "bike" ? "cyclists" : "pedestrians";

  return (
    <div className="grid gap-4">
      <p className="muted m-0 text-[13px]">
        {heat
          ? "Ground temperature from a summer Landsat snapshot. Blue is cool (shade, trees, water), red is hot (heat islands). Hover an area for the value."
          : `Green ribbons are streets the cleanest routes keep using. Numbered pins are busy streets with poor air for ${modeWord}.`}
      </p>
      {!heat && (
        <div className="seg justify-self-start" role="group" aria-label="Travel mode">
          <button aria-pressed={mode === "walk"} onClick={() => setMode("walk")}>Pedestrians</button>
          <button aria-pressed={mode === "bike"} onClick={() => setMode("bike")}>Cyclists</button>
        </div>
      )}
      {!heat && net && (
        <dl className="m-0 grid grid-cols-3 gap-2 text-center">
          {[
            [`${Math.round(net.share_cleaner * 100)}%`, "of trips have a cleaner option"],
            [`${net.when_cleaner.median_dose_change_pct}%`, "exposure, where one exists"],
            [`+${net.when_cleaner.median_time_change_pct}%`, "time, where one exists"],
          ].map(([v, l]) => (
            <div key={l} className="stat">
              <dd className="m-0 text-lg font-bold tabular-nums"><span className="display">{v}</span></dd><dt className="faint text-[11px] leading-tight">{l}</dt>
            </div>
          ))}
        </dl>
      )}
      {heat && ex && <AreaLists ex={ex} titles={["Coolest spots", "Hottest spots"]} colors={["#3f8fc0", "#c9604c"]} fmt={(x) => `${x} °C`} onFly={fly} />}

      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {!heat && !net && !err && <div className="card h-32 animate-pulse" />}
      {!heat && net && showChoke && (
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

      <div>
        <Acc title="About this layer">
          <p className="faint m-0 text-[11px] leading-snug">
            {heat ? "Summer satellite snapshot, not live. Surface, not air, temperature. "
              : `${mode === "bike" ? "Cycling: 15 km/h, steps removed, exposure uses an assumed 2× breathing rate. " : "Walking: 4.8 km/h. "}400 seeded random trips. Measures are suggestions mapped to the Sofia climate plan (PDUEK), not engineering assessments. `}
            {areas?.note}
          </p>
        </Acc>
      </div>
    </div>
  );
}
