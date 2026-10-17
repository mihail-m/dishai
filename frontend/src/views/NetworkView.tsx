import { useEffect, useMemo, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { AreasResp, Chokepoint, Mode, NetworkResp, Scenario } from "../types";
import type { Stage } from "../stage";
import { useHourlyPreload } from "../forecast";
import { api2 } from "../api";
import { caiCss, relCss } from "../map/cai";
import { areaLayer, areaRows, chokepointLayers, flowLayer, heatColor, segmentLayers } from "../map/layers";

interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

export const MEASURE_COLOR: Record<string, string> = { "A.4.3": "#2a7ab5", "A.4.7": "#4a6a8a", "C.1.1": "#6a3fb5", "A.4.4": "#1f7a5c" };
const TIP = { backgroundColor: "#111827", color: "#fff", fontSize: "12px", borderRadius: "8px", padding: "8px 10px", lineHeight: "1.4" };
const rgb = (c: number[]) => `rgb(${c.join(",")})`;

function Toggle({ on, set, label, hint }: { on: boolean; set: (b: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-xs" title={hint}>
      <input type="checkbox" className="mt-0.5" checked={on} onChange={(e) => set(e.target.checked)} />
      <span>{label}{hint && <span className="faint block text-[11px]">{hint}</span>}</span>
    </label>
  );
}

export default function NetworkView({ stage, scenario, hourIndex }: Props) {
  const [mode, setMode] = useState<Mode>("walk");
  const [metric, setMetric] = useState<"air" | "heat">("air");
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
  const heat = metric === "heat";

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
    if (!flow || !net) return;
    let raf = 0, last = 0;
    const tick = (now: number) => { if (now - last > 40) { last = now; setT(now / 6000); } raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [flow, net]);

  const rows = useMemo(() => (areas ? areaRows(areas) : []), [areas]);
  const layers = useMemo(() => {
    const out = [];
    if (showAreas && areas) out.push(areaLayer(areas, rows, scale));
    if (net) {
      out.push(...segmentLayers(net, { channels: showChannels, other: showOther }));
      if (showChoke) out.push(...chokepointLayers(net, sel));
      if (flow) out.push(flowLayer(net, t));
    }
    return out;
  }, [areas, rows, scale, net, showAreas, showChannels, showChoke, showOther, sel, flow, t]);

  const tooltip = useMemo(() => (info: PickingInfo) => {
    const o = info.object as never as Record<string, number> | undefined;
    if (!o) return null;
    if (info.layer?.id === "areas" && areas) {
      const km = (o.street_m / 1000).toFixed(1);
      const html = areas.metric === "heat"
        ? `<b>Surface ${o.mean} °C</b><br/>${o.anomaly > 0 ? "+" : ""}${o.anomaly} °C vs city median (${areas.reference} °C)<br/>${km} km of streets · summer snapshot`
        : `<b>Area of ~${areas.cell_m} m</b><br/>Clean Air Index <b>${Math.round(o.mean)}</b> (cleanest street ${Math.round(o.max)}, worst ${Math.round(o.min)})<br/>PM2.5 ≈ ${o.pm25} µg/m³<br/>${km} km of streets`;
      return { html, style: TIP };
    }
    if (info.layer?.id === "net-channel") return { html: `<b>Clean channel</b><br/>CAI ${o.cai} · used by ${o.usage_clean} of 400 cleanest routes`, style: TIP };
    return null;
  }, [areas]);

  const legend = useMemo(() => {
    const range = areas ? Math.max(Math.abs(areas.thresholds.cool ?? 0), Math.abs(areas.thresholds.hot ?? 0), 1) : 5;
    const bar = heat
      ? `linear-gradient(90deg, ${[-1, -0.5, 0, 0.5, 1].map((u) => rgb(heatColor(u * range, range))).join(",")})`
      : `linear-gradient(90deg, ${[0, 25, 50, 75, 100].map((x) => (scale === "relative" ? relCss(x) : caiCss(x))).join(",")})`;
    return (
      <ul className="grid gap-1.5">
        {showAreas && (
          <li>
            <div className="mb-0.5 font-semibold">{heat ? "Areas: surface temperature" : `Areas: air quality (${mode === "bike" ? "cyclists" : "pedestrians"})`}</div>
            <div className="h-2 w-44 rounded" style={{ background: bar }} />
            <div className="faint flex w-44 justify-between">{heat ? <><span>cool</span><span>city median</span><span>hot</span></> : <><span>dirtier</span><span>{scale === "relative" ? "relative" : "CAI scale"}</span><span>cleaner</span></>}</div>
            <div className="faint">Strong colour = {heat ? "coolest / hottest 10 %" : "cleanest / dirtiest 10 %"}</div>
          </li>
        )}
        {showChannels && <li className="flex items-center"><i className="mr-1.5 inline-block h-[7px] w-6 shrink-0 rounded-full" style={{ background: "#00695c", boxShadow: "0 0 0 2px #fff, 0 0 0 3px #9ca3af" }} />Clean channel: well used by the cleanest routes, cleaner than most streets</li>}
        {showChoke && <li className="flex items-center"><i className="mr-1.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white" style={{ background: "#d6288c" }}>1</i>Chokepoint: many fastest routes, poor air</li>}
        {flow && <li className="flex items-center"><i className="mr-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: "#fff", border: "1.5px solid #6b7280" }} />Moving dots: illustrated direction of the cleanest routes, not live traffic</li>}
        {heat && <li className="faint">Summer satellite snapshot, not live. Surface, not air, temperature.</li>}
      </ul>
    );
  }, [areas, heat, mode, scale, showAreas, showChannels, showChoke, flow]);

  useEffect(() => {
    set({
      layers, base: "off", cursor: undefined, tooltip, legend,
      onPick: (info) => { if (info.layer?.id === "net-choke-dot" && info.object) setSel(info.object as Chokepoint); },
    });
  }, [layers, tooltip, legend, set]);
  useEffect(() => () => set({ layers: [], base: "off" }), [set]);

  const fly = (lon: number, lat: number) => stage.map.current?.flyTo(lon, lat, 15);
  const choose = (c: Chokepoint) => { setSel(c); fly(c.lon, c.lat); };
  const ex = areas?.extremes;
  const labels = heat ? ["Coolest spots", "Hottest spots"] : ["Cleanest areas", "Dirtiest areas"];
  const fmt = (v: number) => (heat ? `${v} °C` : `CAI ${Math.round(v)}`);

  return (
    <div className="grid gap-3">
      <div className="card grid gap-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold">Clean-air network <span className="faint font-normal">· indicative</span></div>
          <div className="seg" role="group" aria-label="Travel mode">
            <button aria-pressed={mode === "walk"} onClick={() => setMode("walk")}>🚶 Pedestrians</button>
            <button aria-pressed={mode === "bike"} onClick={() => setMode("bike")}>🚴 Cyclists</button>
          </div>
        </div>
        <div className="seg w-full" role="group" aria-label="Map view">
          <button className="flex-1" aria-pressed={!heat} onClick={() => setMetric("air")}>Air quality</button>
          <button className="flex-1" aria-pressed={heat} onClick={() => setMetric("heat")}>Cool and hot spots</button>
        </div>
        <p className="muted text-xs leading-snug">
          {heat
            ? "Surface temperature from Landsat (summer snapshot): blue = cool spots (shade, trees, water), red = hot spots (heat islands). Hover an area for the value."
            : `Colour = how clean the air is for ${mode === "bike" ? "cyclists" : "pedestrians"} in each ~250 m area. Dark teal ribbons are clean channels: streets the cleanest routes keep using. Hover anything for details.`}
        </p>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2">
          <Toggle on={showAreas} set={setShowAreas} label="Areas" />
          <Toggle on={showChannels} set={setShowChannels} label="Clean channels" />
          <Toggle on={showChoke} set={setShowChoke} label="Chokepoints" />
          <Toggle on={showOther} set={setShowOther} label="Other busy streets" />
          <Toggle on={flow} set={setFlow} label="Animate route flow" />
          {!heat && (
            <div className="seg justify-self-start" role="group" aria-label="Area colour scale">
              <button aria-pressed={scale === "relative"} onClick={() => setScale("relative")} title="Stretch the 5th-95th percentile of areas to the full colour range">Relative</button>
              <button aria-pressed={scale === "absolute"} onClick={() => setScale("absolute")} title="Absolute Clean Air Index">Absolute</button>
            </div>
          )}
        </div>
        {net && (
          <dl className="grid grid-cols-3 gap-2 text-center">
            {[
              [`${Math.round(net.share_cleaner * 100)}%`, "of trips have a cleaner option"],
              [`${net.when_cleaner.median_dose_change_pct}%`, "exposure, where one exists"],
              [`+${net.when_cleaner.median_time_change_pct}%`, "time, where one exists"],
            ].map(([v, l]) => (
              <div key={l} className="rounded-lg p-2" style={{ background: "var(--surface-2)" }}>
                <dd className="text-lg font-semibold tabular-nums">{v}</dd><dt className="faint text-[10px] leading-tight">{l}</dt>
              </div>
            ))}
          </dl>
        )}
      </div>

      {ex && (
        <div className="card p-3">
          <div className="grid gap-3">
            {[[labels[0], ex.best, "#2a9d6f"], [labels[1], ex.worst, "#d6453d"]].map(([title, list, color]) => (
              <div key={title as string}>
                <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color as string }} />{title as string}</div>
                <ol className="grid gap-1">
                  {(list as AreasResp["extremes"]["best"]).map((p, i) => (
                    <li key={i}>
                      <button className="flex w-full items-baseline justify-between gap-2 rounded-md px-1.5 py-1 text-left text-[11px] hover:bg-[var(--surface-2)]" onClick={() => fly(p.lon, p.lat)}>
                        <span className="truncate">{p.place}</span><span className="shrink-0 tabular-nums font-semibold">{fmt(p.value)}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
          <p className="faint mt-2 text-[11px]">{areas?.note} Click a place to zoom to it.</p>
        </div>
      )}

      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {!net && !err && <div className="card h-40 animate-pulse" />}
      {net && showChoke && (
        <div className="card p-3">
          <div className="mb-2 text-sm font-semibold">Chokepoints <span className="faint text-xs font-normal">fastest-route use × (1 − CAI/100)</span></div>
          <ol className="grid gap-1.5">
            {net.chokepoints.map((c) => (
              <li key={c.rank}>
                <button className="w-full rounded-lg p-2 text-left" style={{ background: sel?.rank === c.rank ? "var(--surface-2)" : "transparent", border: "1px solid var(--line)" }} onClick={() => choose(c)}>
                  <div className="flex items-center gap-2">
                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white" style={{ background: "#d6288c" }}>{c.rank}</span>
                    <span className="truncate text-sm font-semibold">{c.street}</span>
                    <span className="ml-auto flex items-center gap-1 text-xs tabular-nums"><i className="inline-block h-2 w-2 rounded-full" style={{ background: caiCss(c.mean_cai) }} />CAI {Math.round(c.mean_cai)}</span>
                  </div>
                  <div className="faint mt-0.5 text-[11px]">{c.street_bg} · {c.district} · {c.road_class}</div>
                  {sel?.rank === c.rank && (
                    <ul className="mt-1.5 grid gap-1">
                      {c.measures.map((m) => (
                        <li key={m.id} className="text-[11px] leading-snug">
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
      <p className="faint text-[11px]">
        {mode === "bike" ? "Cycling: 15 km/h, steps removed, exposure uses an assumed 2× breathing rate. " : "Walking: 4.8 km/h. "}
        400 seeded random trips. Measures are suggestions mapped to the Sofia climate plan (PDUEK), not engineering assessments.
      </p>
    </div>
  );
}
