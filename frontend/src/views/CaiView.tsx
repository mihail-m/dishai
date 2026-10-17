import { useEffect, useMemo, useState } from "react";
import type { AreasResp, Forecast, Scenario, EdgeExplain, GoogleAir, IncidentsResp, TrafficResp } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { caiCss, relCss } from "../map/cai";
import { useHourlyPreload } from "../forecast";
import { areaLayer, areaRows, highlightLayer, trafficLayers, trafficTooltip } from "../map/layers";
import Explain from "../components/Explain";
import Timeline from "../components/Timeline";
import ValidationCard from "../components/ValidationCard";
import ReferenceCard from "../components/ReferenceCard";
import TrafficCard from "../components/TrafficCard";
import GoogleAirCard from "../components/GoogleAirCard";

interface Props { stage: Stage; fc: Forecast | null; scenario: Scenario; hourIndex: number; setHourIndex: (i: number) => void }

const LEGEND = [[100, "clean"], [80, "good"], [60, "moderate"], [40, "poor"], [20, "bad"], [0, "worst"]] as const;
const REL_LEGEND = ["dirtier than most", "typical", "cleaner than most"] as const;

export default function CaiView({ stage, fc, scenario, hourIndex, setHourIndex }: Props) {
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

  useEffect(() => {
    set({
      layers: [...(detail === "area" && areas ? [areaLayer(areas, rows, stage.relative ? "relative" : "absolute")] : []),
        ...(liveTraffic ? trafficLayers(traffic, incidents) : []), ...(detail === "street" && edge ? [highlightLayer(edge.geometry)] : [])],
      base: detail === "street" ? "full" : "off", cursor: undefined,
      onPick: (info) => {
        if (detail === "street" && info.layer?.id === "cai" && info.index >= 0) setEdgeId(info.index);
      },
      tooltip: (info) => {
        const o = info.object as { mean?: number; min?: number; max?: number; pm25?: number; street_m?: number } | undefined;
        if (detail === "area" && info.layer?.id === "areas" && o && areas) {
          return { html: `<b>Area of ~${areas.cell_m} m</b><br/>Clean Air Index <b>${Math.round(o.mean ?? 0)}</b><br/>PM2.5 ≈ ${o.pm25} µg/m³ (typical for the area)<br/>${((o.street_m ?? 0) / 1000).toFixed(1)} km of streets` };
        }
        return info.object ? trafficTooltip(info as never) : null;
      },
    });
    return () => set({ layers: [], base: "off" });
  }, [set, scenario, hourIndex, edge, traffic, incidents, liveTraffic, detail, areas, rows, stage.relative]);

  useEffect(() => setEdgeId(null), [scenario, detail]);
  const v = stage.values;

  return (
    <div className="grid gap-3">
      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold">Clean Air Index <span className="faint font-normal">· {detail === "area" ? "neighbourhoods (~250 m)" : "street level, indicative"}</span></div>
          <div className="seg" role="group" aria-label="Map detail">
            <button aria-pressed={detail === "area"} onClick={() => setDetail("area")} title="Average of the streets in each 250 m square: what the sensors can support">Neighbourhoods</button>
            <button aria-pressed={detail === "street"} onClick={() => setDetail("street")} title="One colour per street segment. The sensors cannot tell neighbouring streets apart, so treat as indicative">Streets (indicative)</button>
          </div>
          <div className="seg" role="group" aria-label="Colour scale">
            <button aria-pressed={!stage.relative} onClick={() => stage.setRelative(false)} title="Absolute CAI as defined">Absolute</button>
            <button aria-pressed={stage.relative} onClick={() => stage.setRelative(true)} title="Compare streets with each other today (soft colours)">Relative</button>
          </div>
        </div>
        {stage.relative ? (
          <>
            <div className="mt-2 h-2.5 w-full rounded" style={{ background: `linear-gradient(90deg, ${[0, 25, 50, 75, 100].map((x) => relCss(x)).join(",")})` }} />
            <div className="faint mt-1 flex justify-between text-[11px]">{REL_LEGEND.map((l) => <span key={l}>{l}</span>)}</div>
          </>
        ) : (
          <>
            <div className="mt-2 h-2.5 w-full rounded" style={{ background: `linear-gradient(90deg, ${[0, 20, 40, 60, 80, 100].map((x) => caiCss(x)).join(",")})` }} />
            <div className="faint mt-1 flex justify-between text-[11px]">{[...LEGEND].reverse().map(([n, l]) => <span key={n}>{n} {l}</span>)}</div>
          </>
        )}
        {detail === "street" && (
          <p className="mt-2 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--watch-bg)", color: "var(--watch)" }}>
            Street colours are indicative. In winter tests, a map of 250 m squares ranks held-out sensors as well as the street map does, so the extra street detail is not backed by the sensors.
          </p>
        )}
        <p className="faint mt-2 text-[11px] leading-snug">
          100 = cleanest and coolest. One score per street segment and hour from background PM2.5, traffic, trapped air, greenery and (in summer) heat.
          {" "}Absolute anchors on health guidelines: PM2.5 of 5 µg/m³ (WHO annual) scores 80, 15 (WHO 24 h) scores 60, 25 (EU 24 h limit) scores 40.
          {stage.relative ? " Relative stretches the middle 90 % of streets over a soft colour range, so it shows which streets are better or worse than usual today, not how healthy the air is. Pale halos around single sensors come from the local sensor correction, not from cleaner air." : ""}
        </p>
        <p className="faint mt-1 text-[11px]">Greenery: Sofia tree inventory (Sofia Municipality / Sofiaplan, gis.sofiaplan.bg) and Sentinel-2. Heat: Landsat 8/9.</p>
        {!!stage.sensors?.airthings?.count && <p className="faint mt-1 text-[11px]">Blue-ringed dots: {stage.sensors.airthings.attribution}. Hourly values; {stage.sensors.airthings.count} stations in Sofia.</p>}
        {v && <p className="muted mt-1 text-xs tabular-nums">Background {v.background} µg/m³ · sensor bias ×{v.bias} · stagnation {Math.round(v.stagnation)} · heat weight {v.heat_weight}</p>}
      </div>
      {detail === "street" && <Explain e={edge} onClose={() => setEdgeId(null)} />}
      {scenario === "live" && <TrafficCard t={traffic} inc={incidents} show={showTraffic} setShow={setShowTraffic}
        hour={Number(window?.forecast.hours[hourIndex]?.time.slice(11, 13) ?? 12)}
        weekend={[0, 6].includes(new Date(`${window?.forecast.hours[hourIndex]?.time ?? ""}:00`).getDay())} />}
      <ReferenceCard ref_={stage.reference} />
      {scenario === "live" && <GoogleAirCard g={gair} ours={window?.forecast.hours[0]?.pm25_model ?? null} />}
      <ValidationCard key={window?.id} scenario={scenario} />
      {fc && (
        <div className="card p-4 lg:hidden"><Timeline fc={fc} hourIndex={hourIndex} onPick={setHourIndex} /></div>
      )}
    </div>
  );
}
