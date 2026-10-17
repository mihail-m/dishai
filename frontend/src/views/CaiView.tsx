import { useEffect, useState } from "react";
import type { Forecast, Scenario, EdgeExplain } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { caiCss, relCss } from "../map/cai";
import { useHourlyPreload } from "../forecast";
import { highlightLayer } from "../map/layers";
import Explain from "../components/Explain";
import Timeline from "../components/Timeline";
import ValidationCard from "../components/ValidationCard";
import ReferenceCard from "../components/ReferenceCard";

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
  const { reportPreload } = stage;
  useEffect(() => {
    reportPreload("map", edgeId == null ? { loaded: window?.hours.length ?? 24, total: window?.hours.length ?? 24, error: null } : history.status);
  }, [reportPreload, edgeId, history.status.loaded, history.status.error]);
  const { set } = stage;

  useEffect(() => {
    set({
      layers: edge ? [highlightLayer(edge.geometry)] : [], base: "full", cursor: undefined,
      onPick: (info) => {
        if (info.layer?.id === "cai" && info.index >= 0) setEdgeId(info.index);
      },
    });
    return () => set({ layers: [], base: "off" });
  }, [set, scenario, hourIndex, edge]);

  useEffect(() => setEdgeId(null), [scenario]);
  const v = stage.values;

  return (
    <div className="grid gap-3">
      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold">Clean Air Index <span className="faint font-normal">· street level, indicative</span></div>
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
        <p className="faint mt-2 text-[11px] leading-snug">
          100 = cleanest and coolest. One score per street segment and hour from background PM2.5, traffic, trapped air, greenery and (in summer) heat.
          {" "}Absolute anchors on health guidelines: PM2.5 of 5 µg/m³ (WHO annual) scores 80, 15 (WHO 24 h) scores 60, 25 (EU 24 h limit) scores 40.
          {stage.relative ? " Relative stretches the middle 90 % of streets over a soft colour range, so it shows which streets are better or worse than usual today, not how healthy the air is. Pale halos around single sensors come from the local sensor correction, not from cleaner air." : ""}
        </p>
        <p className="faint mt-1 text-[11px]">Greenery: Sofia tree inventory (Sofia Municipality / Sofiaplan, gis.sofiaplan.bg) and Sentinel-2. Heat: Landsat 8/9.</p>
        {v && <p className="muted mt-1 text-xs tabular-nums">Background {v.background} µg/m³ · sensor bias ×{v.bias} · stagnation {Math.round(v.stagnation)} · heat weight {v.heat_weight}</p>}
      </div>
      <Explain e={edge} onClose={() => setEdgeId(null)} />
      <ReferenceCard ref_={stage.reference} />
      <ValidationCard key={window?.id} scenario={scenario} />
      {fc && (
        <div className="card p-4 lg:hidden"><Timeline fc={fc} hourIndex={hourIndex} onPick={setHourIndex} /></div>
      )}
    </div>
  );
}
