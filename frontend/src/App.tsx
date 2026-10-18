import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Layer as DeckLayer, PickingInfo } from "@deck.gl/core";
import type { DistrictsFC, ForecastWindow, ReferenceResp, Scenario } from "./types";
import { api, api2 } from "./api";
import type { Stage } from "./stage";
import { retainForecast, type PreloadStatus } from "./forecast";
import BaseMap, { type MapHandle } from "./map/BaseMap";
import { GOOGLE_KEY, preferredMapProvider, saveMapProvider, type MapProvider } from "./map/config";
import { loadGeometry, type Geometry } from "./map/cai";
import { caiLayer, locationLayers, referenceLayers, sensorLayer } from "./map/layers";
import { districtAt, useLocation } from "./location";
import HealthView from "./views/HealthView";
import MapLegend from "./components/MapLegend";
import TimeBar, { type TimeBarMode } from "./components/TimeBar";
import { Icon, Logo, McBtn, McGroup } from "./components/ui";
import { useTheme } from "./theme";
import ExploreView, { LAYERS, type Layer } from "./views/ExploreView";
import RouteView from "./views/RouteView";

type Tab = "explore" | "route" | "me";
const TABS: [Tab, string, keyof typeof Icon][] = [["explore", "Explore", "map"], ["route", "Route", "route"], ["me", "My health", "me"]];
const SCENARIOS: [Scenario, string, string][] = [
  ["live", "Live data", ""],
  ["winter_inversion", "Winter inversion", "Simulated January"],
  ["summer_heatwave", "Summer heatwave", "Simulated July"],
  ["birch_season", "Birch season", "Simulated April"],
  ["serbia_wildfire", "Wildfire in Serbia", "Simulated August"],
];
const PROVIDERS: [MapProvider, string][] = [["maplibre", "MapLibre · OpenFreeMap"], ["leaflet", "Leaflet · OSM"], ...(GOOGLE_KEY ? [["google", "Google Maps"] as [MapProvider, string]] : [])];

export default function App() {
  const [tab, setTab] = useState<Tab>("explore");
  const [layer, setLayer] = useState<Layer>("air");
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
  const [extra, setExtra] = useState<{ layers: DeckLayer[]; base: "full" | "dim" | "points" | "off"; cursor?: string; legend?: Parameters<Stage["set"]>[0]["legend"]; controls?: React.ReactNode; animated?: Parameters<Stage["set"]>[0]["animated"] }>({ layers: [], base: "full" });
  const tooltip = useRef<Parameters<Stage["set"]>[0]["tooltip"]>(undefined);
  const pick = useRef<((i: PickingInfo) => void) | undefined>(undefined);
  const mapHandle = useRef<MapHandle>(null);
  const [provider, setProvider] = useState<MapProvider>(preferredMapProvider);
  const [menu, setMenu] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [theme, toggleTheme] = useTheme();
  const geo = useLocation();
  const flown = useRef(false);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [menu]);

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
    set: (s) => { pick.current = s.onPick; tooltip.current = s.tooltip; setExtra({ layers: s.layers, base: s.base, cursor: s.cursor, legend: s.legend, controls: s.controls, animated: s.animated }); },
    map: mapHandle, geometry, values, sensors, reference, relative, setRelative, forecast: window, reportPreload,
  }), [geometry, values, sensors, reference, relative, window, reportPreload]);
  // views call stage.set from effects; keep that function identity stable
  const setRef = useRef(stage.set);
  const stableStage = useMemo(() => ({ ...stage, set: setRef.current }), [stage]);

  const layers = useMemo(() => {
    const out: DeckLayer[] = [];
    if (geometry && values && (extra.base === "full" || extra.base === "dim")) out.push(caiLayer(geometry, values.cai, relative, extra.base));
    // stations stay visible on the neighbourhood map ("points"): they are the evidence behind the colours
    if (sensors && (extra.base === "full" || extra.base === "points")) out.push(...sensorLayer(sensors.sensors));
    if (reference?.enabled && (extra.base === "full" || extra.base === "points")) out.push(...referenceLayers(reference.stations));
    if (geo.place) out.push(...locationLayers(geo.place));
    return [...out, ...extra.layers];
  }, [geometry, values, sensors, reference, relative, extra, geo.place]);
  // centre the map on the first fix only; after that the user stays in control of the camera
  useEffect(() => {
    if (geo.place && !flown.current) { flown.current = true; mapHandle.current?.flyTo(geo.place.lon, geo.place.lat, 13); }
    if (!geo.place) flown.current = false;
  }, [geo.place]);
  const myDistrict = geo.place ? districtAt(districts, geo.place.lat, geo.place.lon) : null;
  const locate = () => { if (geo.place) mapHandle.current?.flyTo(geo.place.lon, geo.place.lat, 14); else geo.request(); };

  const mapTab = tab === "explore" || tab === "route";
  const expectedView = tab === "route" ? "route" : layer === "air" ? "map" : "network";
  const status = preload?.id === window?.id && preload?.view === expectedView ? preload.status : null;
  const hourlyReady = !!window && (expectedView === "map" ? !status || status.loaded === status.total : !!status && status.loaded === status.total);
  // the forecast graph follows the layer: air quality shows PM2.5 or air trapping, heat shows the temperature forecast, the rest keep a slim time control
  const barMode: TimeBarMode = tab === "explore" && layer === "air" ? "air" : tab === "explore" && layer === "heat" ? "heat" : "compact";
  const chooseHour = (index: number) => { if (hourlyReady) setHourIndex(index); };
  const chooseProvider = (next: MapProvider) => { saveMapProvider(next); setProvider(next); };
  const go = (next: Tab) => { setTab(next); setSheetOpen(false); setMenu(false); };
  const scenarioLabel = SCENARIOS.find(([k]) => k === scenario)?.[1] ?? "Live";

  return (
    <div className="app">
      <header className="topbar">
        <div className="logo"><Logo />dishai</div>
        <nav className="nav" aria-label="Views">
          {TABS.map(([id, label, ic]) => (
            <button key={id} className="nav-item" aria-current={tab === id ? "page" : undefined} onClick={() => go(id)}>{Icon[ic]}{label}</button>
          ))}
        </nav>
        <div className="top-right" ref={menuRef}>
          <button className="theme-btn" onClick={toggleTheme} aria-pressed={theme === "dark"} aria-label="Dark theme" title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>{theme === "dark" ? Icon.sun : Icon.moon}</button>
          <button className="live-btn" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>
            <span className={`live-dot ${scenario === "live" ? "" : "demo"}`} />
            <span className="lbl">{scenario === "live" ? "Live" : `Demo: ${scenarioLabel}`}</span><span aria-hidden="true">▾</span>
          </button>
          {menu && (
            <div className="menu" role="menu" aria-label="Data and settings">
              <h4>Data</h4>
              <button role="menuitemradio" aria-checked={scenario === "live"} onClick={() => { setScenario("live"); setMenu(false); }}><span>Live data</span></button>
              <button role="menuitem" disabled={refreshing} onClick={() => setRefreshCount((n) => n + 1)}>{refreshing ? "Refreshing…" : "Refresh forecast"}</button>
              <h4>Try a demo</h4>
              {SCENARIOS.filter(([k]) => k !== "live").map(([k, label, sub]) => (
                <button key={k} role="menuitemradio" aria-checked={scenario === k} onClick={() => { setScenario(k); setMenu(false); }}><span>{label} demo<small>{sub}</small></span></button>
              ))}
              <h4>Map style</h4>
              {PROVIDERS.map(([k, label]) => (
                <button key={k} role="menuitemradio" aria-checked={provider === k} onClick={() => { chooseProvider(k); setMenu(false); }}>{label}</button>
              ))}
            </div>
          )}
        </div>
      </header>

      {/* The map stays mounted (hidden on the other tabs) so the street layer is not re-uploaded to the GPU. */}
      <div className={`workspace ${mapTab ? "" : "off"}`} data-sheet={sheetOpen ? "open" : "closed"}>
        <aside className="sidebar" aria-label={tab === "route" ? "Route planner" : "Air quality"}>
          <button className="grab" onClick={() => setSheetOpen((o) => !o)} aria-expanded={sheetOpen} aria-label={sheetOpen ? "Collapse panel" : "Expand panel"} />
          <div className="sidebar-in">
            {tab === "explore" && <ExploreView stage={stableStage} fc={fc} scenario={scenario} hourIndex={hourIndex} setHourIndex={chooseHour} layer={layer} geo={geo} district={myDistrict} />}
            {tab === "route" && <RouteView stage={stableStage} scenario={scenario} hourIndex={window?.hours[hourIndex] ?? 0} />}
          </div>
        </aside>
        <div className="mapwrap">
          <BaseMap layers={layers} active={mapTab} handle={mapHandle} cursor={extra.cursor} animated={extra.animated} provider={provider} dark={theme === "dark"} onProviderChange={chooseProvider}
            onClick={(i) => pick.current?.(i)} tooltip={(i) => tooltip.current?.(i) ?? null} />
          {mapTab && (tab === "explore" || extra.controls) && (
            <div className="map-controls">
              {tab === "explore" && <McGroup label="Map layer">{LAYERS.map(([k, l]) => <McBtn key={k} on={layer === k} onClick={() => setLayer(k)}>{l}</McBtn>)}</McGroup>}
              {extra.controls}
            </div>
          )}
          {mapTab && <button className="map-locate" onClick={locate} aria-label={geo.place ? "Centre on my location" : "Show my location"} title={geo.place ? "Centre on my location" : "Show my location"} disabled={geo.status === "asking"}>{Icon.locate}</button>}
          {mapTab && extra.legend && <MapLegend kind={extra.legend.kind} title={extra.legend.title}>{extra.legend.bar}</MapLegend>}
          {!(tab === "explore" && layer === "clean") && <TimeBar mode={barMode} fc={fc} hourIndex={hourIndex} setHourIndex={chooseHour} ready={hourlyReady} progress={status} error={err} />}
        </div>
      </div>

      {tab === "me" && <main className="pageview"><HealthView scenario={scenario} districts={districts} district={district} setDistrict={setDistrict} /></main>}

      <nav className="tabbar" aria-label="Views">
        {TABS.map(([id, label, ic]) => (
          <button key={id} aria-current={tab === id ? "page" : undefined} onClick={() => go(id)}>{Icon[ic]}<span>{label}</span></button>
        ))}
      </nav>
    </div>
  );
}
