import { useEffect, useState } from "react";
import type { Forecast, Scenario, EdgeExplain } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { caiCss } from "../map/cai";
import { highlightLayer } from "../map/layers";
import Explain from "../components/Explain";
import Timeline from "../components/Timeline";
import ValidationCard from "../components/ValidationCard";
import ReferenceCard from "../components/ReferenceCard";

interface Props { stage: Stage; fc: Forecast | null; scenario: Scenario; hourIndex: number; setHourIndex: (i: number) => void }

const LEGEND = [[100, "clean"], [75, "good"], [55, "moderate"], [35, "poor"], [15, "bad"], [0, "worst"]] as const;

export default function CaiView({ stage, fc, scenario, hourIndex, setHourIndex }: Props) {
  const [edge, setEdge] = useState<EdgeExplain | null>(null);
  const { set } = stage;

  useEffect(() => {
    set({
      layers: edge ? [highlightLayer(edge.geometry)] : [], base: "full", cursor: undefined,
      onPick: (info) => {
        if (info.layer?.id === "cai" && info.index >= 0) api2.edge(info.index, scenario, hourIndex).then(setEdge).catch(() => {});
      },
    });
    return () => set({ layers: [], base: "off" });
  }, [set, scenario, hourIndex, edge]);

  useEffect(() => setEdge(null), [scenario]);
  const v = stage.values;

  return (
    <div className="grid gap-3">
      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold">Clean Air Index <span className="faint font-normal">· street level, indicative</span></div>
          <div className="seg" role="group" aria-label="Colour scale">
            <button aria-pressed={!stage.relative} onClick={() => stage.setRelative(false)} title="Absolute CAI as defined">Absolute</button>
            <button aria-pressed={stage.relative} onClick={() => stage.setRelative(true)} title="Stretch the 5th–95th percentile to the full colour range">Relative</button>
          </div>
        </div>
        <div className="mt-2 h-2.5 w-full rounded" style={{ background: `linear-gradient(90deg, ${[0, 15, 35, 55, 75, 100].map((x) => caiCss(x)).join(",")})` }} />
        <div className="faint mt-1 flex justify-between text-[11px]">{[...LEGEND].reverse().map(([n, l]) => <span key={n}>{n} {l}</span>)}</div>
        <p className="faint mt-2 text-[11px] leading-snug">
          100 = cleanest and coolest. One score per street segment and hour from background PM2.5, traffic, trapped air, greenery and (in summer) heat.
          {v && v.background > 60 ? " Background PM2.5 is above 60 µg/m³ so the absolute scale saturates: use Relative to see which streets are worse." : ""}
          {stage.relative ? " Relative exaggerates small differences; pale halos around single sensors come from the local sensor correction, not from cleaner air." : ""}
        </p>
        {v && <p className="muted mt-1 text-xs tabular-nums">Background {v.background} µg/m³ · sensor bias ×{v.bias} · stagnation {Math.round(v.stagnation)} · heat weight {v.heat_weight}</p>}
      </div>
      <Explain e={edge} onClose={() => setEdge(null)} />
      <ReferenceCard ref_={stage.reference} />
      <ValidationCard scenario={scenario} />
      {fc && (
        <div className="card p-4 lg:hidden"><Timeline fc={fc} hourIndex={hourIndex} onPick={setHourIndex} /></div>
      )}
    </div>
  );
}
