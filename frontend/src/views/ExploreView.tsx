import type { Forecast, Scenario } from "../types";
import type { Stage } from "../stage";
import type { useLocation } from "../location";
import StatusCard from "../components/StatusCard";
import ReferenceCard from "../components/ReferenceCard";
import ValidationCard from "../components/ValidationCard";
import { Acc } from "../components/ui";
import AlertStrip from "../components/AlertStrip";
import { AlertsContext, useAlerts } from "../alerts";
import CaiView from "./CaiView";
import NetworkView from "./NetworkView";
import HeatView from "./HeatView";

export const LAYERS: [Layer, string][] = [["air", "Air quality"], ["heat", "Heat"], ["clean", "Clean paths"]];
export type Layer = "air" | "heat" | "clean";

interface Props { stage: Stage; fc: Forecast | null; scenario: Scenario; hourIndex: number; setHourIndex: (i: number) => void; layer: Layer; geo: ReturnType<typeof useLocation>; district: string | null }

/** Explore: one panel for the air, heat and clean-path layers, with the answer first and the evidence behind accordions. */
export default function ExploreView({ stage, fc, scenario, hourIndex, setHourIndex, layer, geo, district }: Props) {
  const window = stage.forecast;
  const { alerts } = useAlerts(scenario);
  return (
    <AlertsContext.Provider value={alerts}>
      <StatusCard fc={fc} hour={fc?.hours[hourIndex]} />
      <AlertStrip alerts={alerts} />
      <div className="peek-hide grid gap-4">
        {layer === "air" ? <CaiView stage={stage} fc={fc} scenario={scenario} hourIndex={hourIndex} geo={geo} district={district} />
          : layer === "heat" ? <HeatView stage={stage} scenario={scenario} hourIndex={window?.hours[hourIndex] ?? 0} />
          : <NetworkView stage={stage} scenario={scenario} hourIndex={window?.hours[hourIndex] ?? 0} layer={layer} />}
        <div>
          {stage.reference?.enabled && <Acc title="Official air stations"><ReferenceCard ref_={stage.reference} /></Acc>}
          <Acc title="How reliable is this?"><ValidationCard key={window?.id} scenario={scenario} /></Acc>
        </div>
      </div>
    </AlertsContext.Provider>
  );
}
