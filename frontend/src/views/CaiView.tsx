import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AreaPlace, AreasResp, Forecast, Scenario, EdgeExplain, GoogleAir, IncidentsResp, TrafficResp } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { caiCss, relCss } from "../map/cai";
import { useHourlyPreload } from "../forecast";
import { areaRows, highlightLayer, trafficLayers, trafficTooltip } from "../map/layers";
import { AreaSurface, areaField, areaPickLayer, prefersReducedMotion, pulseLayers, type Animated } from "../map/surface";
import Suggestions from "../components/Suggestions";
import type { useLocation } from "../location";
import Explain from "../components/Explain";
import GoogleAirCard from "../components/GoogleAirCard";
import TrafficCard from "../components/TrafficCard";
import { Acc, McBtn, McGroup } from "../components/ui";

interface Props { stage: Stage; fc: Forecast | null; scenario: Scenario; hourIndex: number; geo: ReturnType<typeof useLocation>; district: string | null }

export const LEGEND_STOPS = [0, 25, 50, 75, 100];

/** Explore, "Air quality" layer: neighbourhood squares (or indicative streets) coloured by the air quality score. */
export default function CaiView({ stage, fc, scenario, hourIndex, geo, district }: Props) {
  const [edgeId, setEdgeId] = useState<number | null>(null);
  const window = stage.forecast;
  const hour = window?.hours[hourIndex] ?? 0;
  const history = useHourlyPreload<EdgeExplain>(window && edgeId != null ? `${window.id}:edge:${edgeId}` : null,
    window?.hours ?? [], hour, (i, signal) => api2.edge(edgeId!, scenario, i, { forecastId: window!.id, signal }));
  const edge = history.value;
  // Default: neighbourhood cells (250 m). The sensors support how a neighbourhood differs from the city but not one street from the
  // next (docs/PM_MODEL.md, Phase 5): a cell map ranks hidden sensors as well as the street map does. Streets stay as an indicative option.
  const [detail, setDetail] = useState<"area" | "street">("area");
  const areaHist = useHourlyPreload<AreasResp>(window && detail === "area" ? `${window.id}:cai-areas:${scenario}` : null, window?.hours ?? [], hour,
    (i, signal) => api2.areas(scenario, i, "walk", "air", { forecastId: window!.id, signal }));
  const areas = detail === "area" ? areaHist.value : null;
  const rows = useMemo(() => (areas ? areaRows(areas) : []), [areas]);
  // the surface morphs between hours and scales; pings mark the worst places (both drawn by the map every frame)
  const surface = useRef(new AreaSurface()).current;
  const pings = useRef<AreaPlace[]>([]);
  useEffect(() => {
    if (detail !== "area") { surface.set(null); pings.current = []; }
    else if (areas) { surface.set(areaField(areas, stage.relative ? "relative" : "absolute", rows)); pings.current = areas.extremes.worst; } // while the next hour loads, keep what is on screen
  }, [surface, detail, areas, rows, stage.relative]);
  const animated = useCallback<Animated>((now) => {
    const s = surface.frame(now);
    return { under: s.layers, over: pulseLayers(pings.current, now), settled: s.settled && (!pings.current.length || prefersReducedMotion()) };
  }, [surface]);
  const { reportPreload } = stage;
  useEffect(() => {
    if (detail === "area") reportPreload("map", areaHist.status);
    else reportPreload("map", edgeId == null ? { loaded: window?.hours.length ?? 24, total: window?.hours.length ?? 24, error: null } : history.status);
  }, [reportPreload, detail, edgeId, history.status.loaded, history.status.error, areaHist.status.loaded, areaHist.status.error]);
  const { set } = stage;
  const [traffic, setTraffic] = useState<TrafficResp | null>(null);
  const [incidents, setIncidents] = useState<IncidentsResp | null>(null);
  const [showTraffic, setShowTraffic] = useState(true);
  const [gair, setGair] = useState<GoogleAir | null>(null);
  useEffect(() => { api2.googleAir().then(setGair).catch(() => undefined); }, []);
  useEffect(() => {
    let off = false;
    const pull = () => {
      api2.traffic().then((t) => !off && setTraffic(t)).catch(() => undefined);
      api2.incidents().then((i) => !off && setIncidents(i)).catch(() => undefined);
    };
    pull();
    // a first-time build of the corridors takes about a minute, so poll quickly until they exist, then every 3 minutes
    const timer = setInterval(pull, traffic?.ready ? 3 * 60 * 1000 : 15 * 1000);
    return () => { off = true; clearInterval(timer); };
  }, [traffic?.ready]);
  // live traffic is real data: not shown in the simulated demo scenarios
  const liveTraffic = scenario === "live" && showTraffic;

  const legend = useMemo(() => ({
    kind: "air" as const, title: "Air quality score",
    bar: (
      <>
        <div className="ramp mb-1 mt-2" style={{ background: `linear-gradient(90deg, ${LEGEND_STOPS.map((x) => (stage.relative ? relCss(x) : caiCss(x))).join(",")})` }} />
        <div className="faint flex justify-between text-[11px]">
          {stage.relative ? <><span>dirtier than most</span><span>cleaner than most</span></> : <><span>0 worse</span><span>100 cleaner</span></>}
        </div>
      </>
    ),
  }), [stage.relative]);

  const { setRelative } = stage;
  const controls = useMemo(() => (
    <>
      <McGroup label="Map detail">
        <McBtn on={detail === "area"} onClick={() => setDetail("area")} title="Average of the streets in each 250 m square: what the sensors can support">Neighbourhoods</McBtn>
        <McBtn on={detail === "street"} onClick={() => setDetail("street")} title="One colour per street segment. The sensors cannot tell neighbouring streets apart, so treat as indicative">Streets</McBtn>
      </McGroup>
      <McGroup label="Colour scale">
        <McBtn on={!stage.relative} onClick={() => setRelative(false)} title="Absolute score, anchored on health guidelines">Absolute</McBtn>
        <McBtn on={stage.relative} onClick={() => setRelative(true)} title="Compare places with each other today (soft colours)">Relative</McBtn>
      </McGroup>
      {scenario === "live" && traffic?.enabled && <McGroup label="Traffic"><McBtn on={showTraffic} onClick={() => setShowTraffic((x) => !x)} label="Show live traffic">Traffic</McBtn></McGroup>}
    </>
  ), [detail, stage.relative, setRelative, scenario, traffic?.enabled, showTraffic]);

  useEffect(() => {
    set({
      controls,
      animated,
      layers: [...(detail === "area" && areas ? [areaPickLayer(areas, rows)] : []),
        ...(liveTraffic ? trafficLayers(traffic, incidents) : []), ...(detail === "street" && edge ? [highlightLayer(edge.geometry)] : [])],
      base: detail === "street" ? "full" : "points", cursor: undefined, legend,
      onPick: (info) => {
        if (detail === "street" && info.layer?.id === "cai" && info.index >= 0) setEdgeId(info.index);
      },
      tooltip: (info) => {
        const o = info.object as { mean?: number; min?: number; max?: number; pm25?: number; street_m?: number } | undefined;
        if (detail === "area" && info.layer?.id === "areas" && o && areas) {
          return { html: `<b>Area of ~${areas.cell_m} m</b><br/>Air quality score <b>${Math.round(o.mean ?? 0)}</b><br/>PM2.5 ≈ ${o.pm25} µg/m³ (typical for the area)<br/>${((o.street_m ?? 0) / 1000).toFixed(1)} km of streets` };
        }
        return info.object ? trafficTooltip(info as never) : null;
      },
    });
    return () => set({ layers: [], base: "off" });
  }, [set, scenario, hourIndex, edge, traffic, incidents, liveTraffic, detail, areas, rows, stage.relative, legend, controls, animated]);

  useEffect(() => setEdgeId(null), [scenario, detail]);
  const v = stage.values;

  return (
    <div className="grid gap-4">
      <p className="muted m-0 text-[13px]">
        {detail === "area"
          ? "Each square is about 250 m. Greener is cleaner. Hover or tap a square for details."
          : "One colour per street. Street detail is indicative: the sensors cannot tell neighbouring streets apart. Tap a street to see why."}
      </p>
      {detail === "street" && (
        <p className="m-0 rounded-xl px-3 py-2 text-xs" style={{ background: "var(--watch-bg)", color: "var(--watch)" }}>
          Street colours are indicative. In winter tests, a map of 250 m squares ranks held-out sensors as well as the street map does, so the extra street detail is not backed by the sensors.
        </p>
      )}
      {detail === "area" && <Suggestions areas={areas} fc={fc} hourIndex={hourIndex} status={geo.status} place={geo.place} district={district} onLocate={geo.request} onStop={geo.stop} />}
      {detail === "street" && <Explain e={edge} onClose={() => setEdgeId(null)} />}
      {v && <p className="faint m-0 text-[11px] tabular-nums">Background {v.background} µg/m³ · sensor bias ×{v.bias} · air trapping {Math.round(v.stagnation)} · heat weight {v.heat_weight}</p>}

      <div>
        <Acc title="How the score works">
          <p className="muted m-0 text-[13px] leading-snug">
            100 is the cleanest and coolest. One score per street and hour from background PM2.5, trapped air, greenery and (in summer) heat; there is no traffic term.
            Absolute anchors on health guidelines: PM2.5 of 5 µg/m³ (WHO annual) scores 80, 15 (WHO 24 h) scores 60, 25 (EU 24 h limit) scores 40.
            {stage.relative ? " Relative stretches the middle 90 % of places over a soft colour range, so it shows which places are better or worse than usual today, not how healthy the air is." : ""}
          </p>
          <p className="faint m-0 text-[11px]">Greenery: Sofia tree inventory (Sofia Municipality / Sofiaplan, gis.sofiaplan.bg) and Sentinel-2. Heat: Landsat 8/9.</p>
          {!!stage.sensors?.airthings?.count && <p className="faint m-0 text-[11px]">Blue-ringed dots: {stage.sensors.airthings.attribution}. Hourly values; {stage.sensors.airthings.count} stations in Sofia.</p>}
        </Acc>
        {scenario === "live" && traffic?.enabled && (
          <Acc title="Live traffic">
            <TrafficCard t={traffic} inc={incidents} show={showTraffic} setShow={setShowTraffic}
              hour={Number(window?.forecast.hours[hourIndex]?.time.slice(11, 13) ?? 12)}
              weekend={[0, 6].includes(new Date(`${window?.forecast.hours[hourIndex]?.time ?? ""}:00`).getDay())} />
          </Acc>
        )}
        {scenario === "live" && gair?.enabled && (
          <Acc title="Google air quality comparison"><GoogleAirCard g={gair} ours={window?.forecast.hours[0]?.pm25_model ?? null} /></Acc>
        )}
      </div>
    </div>
  );
}
