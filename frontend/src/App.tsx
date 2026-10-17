import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { CaiValues, DistrictsFC, Forecast, ReferenceResp, Scenario, SensorsResp } from "./types";
import { api, api2 } from "./api";
import type { Stage } from "./stage";
import BaseMap, { type MapHandle } from "./map/BaseMap";
import { loadGeometry, type Geometry } from "./map/cai";
import { caiLayer, referenceLayers, sensorLayer } from "./map/layers";
import Banner from "./components/Banner";
import Dashboard from "./components/Dashboard";
import ForYou from "./components/ForYou";
import HourSlider from "./components/HourSlider";
import MapLegend from "./components/MapLegend";
import Timeline from "./components/Timeline";
import CaiView from "./views/CaiView";
import NetworkView from "./views/NetworkView";
import RouteView from "./views/RouteView";

type Tab = "map" | "route" | "network" | "me" | "city";
const TABS: [Tab, string][] = [["map", "Clean Air map"], ["route", "Route planner"], ["network", "Clean-air network"], ["me", "For me"], ["city", "Municipality"]];
const MAP_TABS: Tab[] = ["map", "route", "network"];

export default function App() {
  const [tab, setTab] = useState<Tab>("map");
  const [scenario, setScenario] = useState<Scenario>("live");
  const [fc, setFc] = useState<Forecast | null>(null);
  const [districts, setDistricts] = useState<DistrictsFC | null>(null);
  const [sensors, setSensors] = useState<SensorsResp | null>(null);
  const [reference, setReference] = useState<ReferenceResp | null>(null);
  const [hourIndex, setHourIndex] = useState(0);
  const [district, setDistrict] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [values, setValues] = useState<CaiValues | null>(null);
  const [relative, setRelative] = useState(false);
  const [extra, setExtra] = useState<{ layers: Layer[]; base: "full" | "dim" | "off"; cursor?: string; legend?: React.ReactNode }>({ layers: [], base: "full" });
  const tooltip = useRef<Parameters<Stage["set"]>[0]["tooltip"]>(undefined);
  const pick = useRef<((i: PickingInfo) => void) | undefined>(undefined);
  const mapHandle = useRef<MapHandle>(null);

  const load = useCallback(async (s: Scenario, resetHour: boolean) => {
    try {
      const [f, d, se, ref] = await Promise.all([api.forecast(s), api.districts(s), api.sensors(s), api2.reference(s).catch(() => null)]);
      setFc(f); setDistricts(d); setSensors(se); setReference(ref); setErr(null);
      if (resetHour) {
        // demo scenario: open on the evening rush hour of the warning window (traffic x trapped air shows contrast)
        const start = s !== "live" && f.alert.active && f.alert.start ? f.hours.findIndex((h) => h.time === f.alert.start) : -1;
        setHourIndex(start >= 0 ? start : f.now_index);
      }
      setDistrict((cur) => cur ?? (d.features.find((x) => x.properties.name === "Sredets") ? "Sredets" : d.features[0]?.properties.name ?? null));
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    setFc(null);
    load(scenario, true);
    const t = setInterval(() => load(scenario, false), 5 * 60 * 1000);
    return () => clearInterval(t);
  }, [scenario, load]);

  useEffect(() => { loadGeometry().then(setGeometry).catch((e) => setErr(`Street geometry: ${e}`)); }, []);

  // CAI values per hour (debounced while the slider moves)
  useEffect(() => {
    let off = false;
    const t = setTimeout(() => {
      api2.values(scenario, fc ? hourIndex : undefined).then((v) => !off && setValues(v)).catch(() => {});
    }, 120);
    return () => { off = true; clearTimeout(t); };
  }, [scenario, hourIndex, fc]);

  const stage: Stage = useMemo(() => ({
    set: (s) => { pick.current = s.onPick; tooltip.current = s.tooltip; setExtra({ layers: s.layers, base: s.base, cursor: s.cursor, legend: s.legend }); },
    map: mapHandle, geometry, values, sensors, reference, relative, setRelative,
  }), [geometry, values, sensors, reference, relative]);
  // views call stage.set from effects; keep that function identity stable
  const setRef = useRef(stage.set);
  const stableStage = useMemo(() => ({ ...stage, set: setRef.current }), [stage]);

  const layers = useMemo(() => {
    const out: Layer[] = [];
    if (geometry && values && extra.base !== "off") out.push(caiLayer(geometry, values.cai, relative, extra.base));
    if (sensors && extra.base === "full") out.push(sensorLayer(sensors.sensors));
    if (reference?.enabled && extra.base === "full") out.push(...referenceLayers(reference.stations));
    return [...out, ...extra.layers];
  }, [geometry, values, sensors, reference, relative, extra]);

  const mapTab = MAP_TABS.includes(tab);
  const hourControls = <HourSlider fc={fc} hourIndex={hourIndex} setHourIndex={setHourIndex} />;

  return (
    <div className="mx-auto flex min-h-full max-w-[1360px] flex-col gap-4 px-4 py-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div aria-hidden className="grid h-9 w-9 place-items-center rounded-xl text-white" style={{ background: "var(--warn)" }}>≋</div>
          <div>
            <h1 className="text-lg font-semibold leading-tight">Inversion Watch</h1>
            <div className="faint text-xs">Sofia · from air-quality forecast to action</div>
          </div>
        </div>
        <nav className="flex flex-wrap gap-1" role="tablist" aria-label="Views">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className="tab" onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <div className="seg" role="group" aria-label="Data mode">
          <button aria-pressed={scenario === "live"} onClick={() => setScenario("live")}>Live</button>
          <button aria-pressed={scenario === "winter_inversion"} onClick={() => setScenario("winter_inversion")}>Winter inversion demo</button>
        </div>
      </header>

      <Banner fc={fc} />
      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>Backend not reachable: {err}. Start it with <code>make dev</code>.</div>}

      <main className="flex-1">
        {/* The map stays mounted (hidden on other tabs) so the street layer is not re-uploaded to the GPU. */}
        <div className={mapTab ? "grid gap-4 lg:grid-cols-[1fr_380px]" : "hidden"}>
          <div className="grid content-start gap-3">
            <div className="card relative h-[min(64vh,640px)] min-h-[440px] overflow-hidden">
              {mapTab && <MapLegend tab={tab as "map" | "route" | "network"} custom={extra.legend} />}
              <BaseMap layers={layers} active={mapTab} handle={mapHandle} cursor={extra.cursor} onClick={(i) => pick.current?.(i)} tooltip={(i) => tooltip.current?.(i) ?? null} />
            </div>
            {hourControls}
            {tab === "map" && fc && (
              <section className="card hidden p-4 lg:block"><Timeline fc={fc} hourIndex={hourIndex} onPick={setHourIndex} /></section>
            )}
          </div>
          <aside className="grid content-start gap-3">
            {tab === "map" && <CaiView stage={stableStage} fc={fc} scenario={scenario} hourIndex={hourIndex} setHourIndex={setHourIndex} />}
            {tab === "route" && <RouteView stage={stableStage} scenario={scenario} hourIndex={hourIndex} />}
            {tab === "network" && <NetworkView stage={stableStage} scenario={scenario} hourIndex={hourIndex} />}
          </aside>
        </div>
        {tab === "me" && <ForYou scenario={scenario} districts={districts} district={district} setDistrict={setDistrict} />}
        {tab === "city" && <Dashboard scenario={scenario} active />}
      </main>
    </div>
  );
}
