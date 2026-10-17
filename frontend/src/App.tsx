import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { DistrictsFC, ForecastWindow, ReferenceResp, Scenario } from "./types";
import { api, api2 } from "./api";
import type { Stage } from "./stage";
import { retainForecast, type PreloadStatus } from "./forecast";
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
  const [snapshot, setSnapshot] = useState<ForecastWindow | null>(null);
  const window = snapshot?.forecast.scenario === (scenario === "live" ? null : scenario) ? snapshot : null;
  const fc = window?.forecast ?? null;
  const [refreshCount, setRefreshCount] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [preload, setPreload] = useState<{ id: string; view: string; status: PreloadStatus } | null>(null);
  const lastSnapshot = useRef<ForecastWindow | null>(null);
  const handledRefresh = useRef(0);
  const [districts, setDistricts] = useState<DistrictsFC | null>(null);
  const sensors = window?.sensors ?? null;
  const [reference, setReference] = useState<ReferenceResp | null>(null);
  const [hourIndex, setHourIndex] = useState(0);
  const [district, setDistrict] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const values = window?.values[hourIndex] ?? null;
  const [relative, setRelative] = useState(false);
  const [extra, setExtra] = useState<{ layers: Layer[]; base: "full" | "dim" | "off"; cursor?: string; legend?: React.ReactNode }>({ layers: [], base: "full" });
  const tooltip = useRef<Parameters<Stage["set"]>[0]["tooltip"]>(undefined);
  const pick = useRef<((i: PickingInfo) => void) | undefined>(undefined);
  const mapHandle = useRef<MapHandle>(null);

  useEffect(() => {
    let disposed = false;
    let controller: AbortController | undefined;
    const load = async (force: boolean) => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      setRefreshing(true); setErr(null);
      try {
        const next = await api.window(scenario, force, request.signal);
        if (disposed || request.signal.aborted) return;
        const previous = lastSnapshot.current;
        setHourIndex((index) => {
          const selectedTime = previous?.forecast.scenario === next.forecast.scenario ? previous.forecast.hours[index]?.time : null;
          return Math.max(0, next.forecast.hours.findIndex((h) => h.time === selectedTime));
        });
        retainForecast(next.id);
        lastSnapshot.current = next;
        setSnapshot(next);
        const [d, ref] = await Promise.all([api.districts(scenario).catch(() => null), api2.reference(scenario).catch(() => null)]);
        if (disposed || request.signal.aborted) return;
        setReference(ref);
        if (d) {
          setDistricts(d);
          setDistrict((cur) => cur ?? (d.features.find((x) => x.properties.name === "Sredets") ? "Sredets" : d.features[0]?.properties.name ?? null));
        }
      } catch (error) {
        if (!disposed && !request.signal.aborted) setErr(String(error));
      } finally {
        if (!disposed && !request.signal.aborted) setRefreshing(false);
      }
    };
    const force = refreshCount !== handledRefresh.current;
    handledRefresh.current = refreshCount;
    void load(force);
    const timer = setInterval(() => void load(false), 5 * 60 * 1000);
    return () => { disposed = true; controller?.abort(); clearInterval(timer); };
  }, [scenario, refreshCount]);

  useEffect(() => { loadGeometry().then(setGeometry).catch((e) => setErr(`Street geometry: ${e}`)); }, []);
  const reportPreload = useCallback((view: string, status: PreloadStatus) => {
    if (window) setPreload({ id: window.id, view, status });
  }, [window]);

  const stage: Stage = useMemo(() => ({
    set: (s) => { pick.current = s.onPick; tooltip.current = s.tooltip; setExtra({ layers: s.layers, base: s.base, cursor: s.cursor, legend: s.legend }); },
    map: mapHandle, geometry, values, sensors, reference, relative, setRelative, forecast: window, reportPreload,
  }), [geometry, values, sensors, reference, relative, window, reportPreload]);
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
  const status = preload?.id === window?.id && preload?.view === tab ? preload.status : null;
  const hourlyReady = !!window && (tab === "map" ? !status || status.loaded === status.total : !!status && status.loaded === status.total);
  const chooseHour = (index: number) => { if (hourlyReady) setHourIndex(index); };
  const hourControls = <HourSlider fc={fc} hourIndex={hourIndex} setHourIndex={chooseHour}
    ready={hourlyReady} refreshing={refreshing} onRefresh={() => setRefreshCount((n) => n + 1)}
    progress={status} error={err} />;

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
          <button aria-pressed={scenario === "winter_inversion"} onClick={() => setScenario("winter_inversion")} title="Demo: simulated January inversion">Winter inversion demo</button>
          <button aria-pressed={scenario === "summer_heatwave"} onClick={() => setScenario("summer_heatwave")} title="Demo: simulated July heatwave">Summer heatwave</button>
          <button aria-pressed={scenario === "birch_season"} onClick={() => setScenario("birch_season")} title="Demo: simulated April birch pollen peak">Birch season</button>
        </div>
      </header>

      <Banner fc={fc} />


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
              <section className="card hidden p-4 lg:block"><Timeline fc={fc} hourIndex={hourIndex} onPick={chooseHour} /></section>
            )}
          </div>
          <aside className="grid content-start gap-3">
            {tab === "map" && <CaiView stage={stableStage} fc={fc} scenario={scenario} hourIndex={hourIndex} setHourIndex={chooseHour} />}
            {tab === "route" && <RouteView stage={stableStage} scenario={scenario} hourIndex={window?.hours[hourIndex] ?? 0} />}
            {tab === "network" && <NetworkView stage={stableStage} scenario={scenario} hourIndex={window?.hours[hourIndex] ?? 0} />}
          </aside>
        </div>
        {tab === "me" && <ForYou scenario={scenario} districts={districts} district={district} setDistrict={setDistrict} />}
        {tab === "city" && <Dashboard scenario={scenario} active />}
      </main>
    </div>
  );
}
