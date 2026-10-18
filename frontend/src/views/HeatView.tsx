import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { AreasResp, HeatLevel, HeatScale, Scenario } from "../types";
import type { Stage } from "../stage";
import { useHourlyPreload } from "../forecast";
import { api2, hhmm } from "../api";
import { areaRows, heatColor } from "../map/layers";
import { AreaSurface, areaField, areaPickLayer, type Animated } from "../map/surface";
import AreaLists from "../components/AreaLists";
import { Acc, McBtn, McGroup } from "../components/ui";

interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

const TIP = { backgroundColor: "#111827", color: "#fff", fontSize: "12px", borderRadius: "8px", padding: "8px 10px", lineHeight: "1.4" };
const rgb = (c: number[]) => `rgb(${c.join(",")})`;
const LEVEL_TEXT: Record<HeatLevel, [string, string]> = {
  none: ["No heat stress", "Comfortable or cool outside."],
  caution: ["Warm", "Take breaks in the shade and drink water."],
  high: ["Hot", "Avoid exertion in the afternoon; check on older neighbours."],
  extreme: ["Extreme heat", "Stay in the coolest place you can, especially at midday."],
};
const sign = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`;

/** Explore, "Heat" layer: feels-like temperature of every area for the selected hour, 48 hours ahead (city forecast plus a learned local offset). */
export default function HeatView({ stage, scenario, hourIndex }: Props) {
  const [scale, setScale] = useState<HeatScale>("absolute");
  const { set } = stage;
  const window = stage.forecast;
  const key = window ? `${window.id}:heat:${scenario}:${scale}` : null;
  const history = useHourlyPreload<AreasResp>(key, window?.hours ?? [], hourIndex,
    (hour, signal) => api2.areas(scenario, hour, "walk", "heat", { forecastId: window!.id, signal, heatScale: scale }));
  const areas = history.value;
  const { reportPreload } = stage;
  useEffect(() => { reportPreload("network", history.status); }, [reportPreload, key, history.status.loaded, history.status.error]);

  const rows = useMemo(() => (areas ? areaRows(areas) : []), [areas]);
  const surface = useRef(new AreaSurface()).current;
  useEffect(() => { if (areas) surface.set(areaField(areas, "absolute", rows)); }, [surface, areas, rows]); // while the next hour loads, keep what is on screen
  useEffect(() => () => surface.set(null), [surface]);
  const animated = useCallback<Animated>((now) => {
    const s = surface.frame(now);
    return { under: s.layers, over: [], settled: s.settled };
  }, [surface]);

  const tooltip = useMemo(() => (info: PickingInfo) => {
    const o = info.object as never as Record<string, number> | undefined;
    if (!o || info.layer?.id !== "areas" || !areas) return null;
    const off = o.offset ?? 0;
    return { html: `<b>Feels like ${Math.round(o.mean)} °C</b><br/>Air ${Math.round(o.air ?? o.mean)} °C · ${sign(off)} °C vs the city average<br/>${(o.street_m / 1000).toFixed(1)} km of streets`, style: TIP };
  }, [areas]);

  const legend = useMemo(() => {
    const range = areas ? Math.max(Math.abs(areas.thresholds.cool ?? 0), Math.abs(areas.thresholds.hot ?? 0), 1) : 20;
    const centre = areas?.centre ?? 18;
    const abs = scale === "absolute";
    const bar = `linear-gradient(90deg, ${[-1, -0.5, 0, 0.5, 1].map((u) => rgb(heatColor(u * range, range))).join(",")})`;
    return {
      kind: "heat" as const, title: abs ? "Feels-like temperature" : "Warmer or cooler than the city",
      bar: (
        <>
          <div className="ramp mb-1 mt-2" style={{ background: bar }} />
          <div className="faint flex justify-between text-[11px] tabular-nums">
            {abs ? <><span>{Math.round(centre - range)}°</span><span>{Math.round(centre)}°</span><span>{Math.round(centre + range)}°</span></> : <><span>cooler</span><span>warmer</span></>}
          </div>
        </>
      ),
    };
  }, [areas, scale]);

  const controls = useMemo(() => (
    <McGroup label="Heat colour scale">
      <McBtn on={scale === "absolute"} onClick={() => setScale("absolute")} title="Real temperatures: blue is cold, red is hot, on a fixed scale">Temperature</McBtn>
      <McBtn on={scale === "relative"} onClick={() => setScale("relative")} title="Differences between places: which areas are warmer or cooler than the city right now">Differences</McBtn>
    </McGroup>
  ), [scale]);

  useEffect(() => {
    set({ layers: areas ? [areaPickLayer(areas, rows)] : [], base: "off", cursor: undefined, tooltip, legend, controls, animated, onPick: undefined });
  }, [areas, rows, tooltip, legend, controls, animated, set]);
  useEffect(() => () => set({ layers: [], base: "off" }), [set]);

  const fly = (lon: number, lat: number) => stage.map.current?.flyTo(lon, lat, 15);
  const err = history.status.error;
  const city = areas?.city;
  const [head, sub] = city ? LEVEL_TEXT[city.level] : ["", ""];
  // hottest hour of the next 24 h from the city forecast (what the time bar draws)
  const ahead = (stage.forecast?.forecast.hours ?? []).slice(hourIndex, hourIndex + 24);
  const peak = ahead.reduce<{ v: number; t: string } | null>((a, h) => { const v = h.apparent ?? h.t2m; return !a || v > a.v ? { v, t: h.time } : a; }, null);
  const spread = areas ? areas.stats.p90 - areas.stats.p10 : 0;

  return (
    <div className="grid gap-4">
      {city ? (
        <section className="card grid gap-1 p-4" aria-live="polite">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="m-0 text-xl font-bold">{head}</h2>
            <span className="display text-3xl font-bold tabular-nums">{Math.round(city.feels)}°</span>
          </div>
          <p className="muted m-0 text-sm">{sub} Feels-like in the city at {hhmm(areas!.time ?? "")}; air {Math.round(city.air)} °C.</p>
          {peak && <p className="faint m-0 text-xs">Warmest point in the next 24 hours: {Math.round(peak.v)} °C around {hhmm(peak.t)}.</p>}
        </section>
      ) : !err ? <div className="card h-28 animate-pulse" /> : null}
      <p className="muted m-0 text-[13px]">
        {areas?.model === false
          ? "The local temperature model is not available, so every place shows the city forecast."
          : `Each area is the city forecast plus how much warmer or cooler it usually is in this weather. Today the warmest and coolest areas differ by about ${Math.max(1, Math.round(spread))} °C; hover an area for the value.`}
      </p>
      {areas && <AreaLists ex={areas.extremes} titles={["Coolest areas", "Warmest areas"]} colors={["#3f8fc0", "#c9604c"]} fmt={(x) => `${Math.round(x)} °C`} onFly={fly} />}
      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      <div>
        <Acc title="How is this calculated?">
          <p className="faint m-0 text-[11px] leading-snug">
            {areas?.note} The offset uses building height within 1 km, ground height and height above the surroundings, and depends on sunshine, cloud and wind. It was learned from about {areas?.validation?.sensors ?? 220} citizen
            temperature sensors and tested on sensors it never saw: it orders places correctly better than chance (rank correlation 0.3 overall, 0.4 on summer nights, only 0.15 on sunny afternoons, where sensors in direct sun are noisy).
            Satellite surface temperature was tested and dropped: it did not match measured air temperature differences.
          </p>
        </Acc>
      </div>
    </div>
  );
}
