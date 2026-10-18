import { useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { Mode, RouteResp, Scenario } from "../types";
import type { Stage } from "../stage";
import { useHourlyPreload } from "../forecast";
import { api2 } from "../api";
import { PRESET_PAIRS, GOOGLE_KEY } from "../map/config";
import { markerLayer, routeLayers } from "../map/layers";
import PlaceSearch from "../components/PlaceSearch";

type LL = [number, number]; // lat, lng
interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

// One route, tuned for the cleanest air within the time cap the router allows: the only choice is how you travel.
const LAMBDA = 3;

export default function RouteView({ stage, scenario, hourIndex }: Props) {
  const [from, setFrom] = useState<LL | null>(PRESET_PAIRS[1].from as unknown as LL);
  const [to, setTo] = useState<LL | null>(PRESET_PAIRS[1].to as unknown as LL);
  const [picking, setPicking] = useState<"from" | "to" | null>(null);
  const [googleResult, setGoogleResult] = useState<{ key: string; value: RouteResp } | null>(null);
  const [mode, setMode] = useState<Mode>("walk");
  const [labels, setLabels] = useState<{ from: string; to: string }>({ from: PRESET_PAIRS[1].label.split(" → ")[0].split(": ").pop() ?? "", to: PRESET_PAIRS[1].label.split(" → ")[1] ?? "" });
  const pickRef = useRef(picking);
  pickRef.current = picking;
  const { set } = stage;

  const window = stage.forecast;
  const routeKey = window && from && to ? `${window.id}:route:${mode}:${from.join()}:${to.join()}:${LAMBDA}` : null;
  const history = useHourlyPreload<RouteResp>(routeKey, window?.hours ?? [], hourIndex,
    (hour, signal) => api2.route(from!, to!, LAMBDA, scenario, hour, mode, { forecastId: window!.id, signal, includeGoogle: false }), 220);
  const googleKey = `${routeKey}:${hourIndex}`;
  const res = googleResult?.key === googleKey ? googleResult.value : history.value;
  const err = history.status.error;
  const busy = history.status.loaded < history.status.total;
  const { reportPreload } = stage;
  useEffect(() => { reportPreload("route", history.status); }, [reportPreload, routeKey, history.status.loaded, history.status.error]);
  useEffect(() => {
    if (res) setLabels({ from: res.from_label, to: res.to_label });
  }, [res]);
  // Preload our own routes; optional billed Google requests only enrich the
  // currently selected hour, never all 24 hours in the background.
  useEffect(() => {
    if (!history.value?.google_available || !from || !to || !window) return;
    const controller = new AbortController();
    const ownRoute = history.value;
    api2.route(from, to, LAMBDA, scenario, hourIndex, mode, { forecastId: window.id, signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setGoogleResult({ key: googleKey, value }); })
      .catch(() => { if (!controller.signal.aborted) setGoogleResult({ key: googleKey, value: { ...ownRoute, google_status: "error" } }); });
    return () => controller.abort();
  }, [googleKey, history.value?.google_available]);

  const layers = useMemo(() => {
    const lines: { name: string; line: [number, number][]; width?: number }[] = [];
    if (res) {
      lines.push({ name: "fastest", line: res.routes.fastest.polyline, width: 4 });
      lines.push({ name: "cleanest", line: res.snapped.used ? res.snapped.polyline : res.routes.cleanest.polyline, width: 8 });
    }
    const pts = [];
    if (from) pts.push({ id: "a", lonlat: [from[1], from[0]] as [number, number], label: "A", color: [31, 111, 92] as [number, number, number] });
    if (to) pts.push({ id: "b", lonlat: [to[1], to[0]] as [number, number], label: "B", color: [192, 57, 43] as [number, number, number] });
    return [...routeLayers(lines), ...markerLayer(pts)];
  }, [res, from, to]);

  useEffect(() => {
    set({
      layers, base: "dim", cursor: picking ? "crosshair" : undefined,
      legend: { kind: "route", title: "Routes", bar: <div className="mt-1.5 grid gap-1"><span className="flex items-center gap-2"><i className="inline-block h-1 w-5 rounded" style={{ background: "#1f8a70" }} />Cleanest</span><span className="flex items-center gap-2"><i className="inline-block w-5" style={{ borderTop: "3px solid #4a5563" }} />Fastest</span></div> },
      onPick: (info: PickingInfo) => {
        const p = pickRef.current;
        if (!p || !info.coordinate) return;
        (p === "from" ? setFrom : setTo)([info.coordinate[1], info.coordinate[0]]);
        setLabels((l) => ({ ...l, [p]: "" })); // stale name would mislead until the route returns the new place name
        setPicking(null);
      },
    });
    return () => set({ layers: [], base: "off" });
  }, [layers, picking, set]);

  useEffect(() => {
    if (!res) return;
    const xs = res.routes.fastest.polyline.map((p) => p[0]), ys = res.routes.fastest.polyline.map((p) => p[1]);
    stage.map.current?.fitBounds(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [res?.from.join(), res?.to.join()]);

  const main = res?.routes.cleanest ?? null;
  const noBetter = !!res?.confidence && !res.confidence.cleaner_exists;
  const gain = main ? Math.abs(Math.round(main.dose_change_pct)) : 0;
  const place = (v: LL | null, label: string, fallback: string) => (v ? label || v.map((x) => x.toFixed(4)).join(", ") : fallback);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-xl font-bold">Plan a cleaner {mode === "bike" ? "ride" : "walk"}</h2>
        <div className="seg" role="group" aria-label="Travel mode">
          <button aria-pressed={mode === "walk"} onClick={() => setMode("walk")}>Walk</button>
          <button aria-pressed={mode === "bike"} onClick={() => setMode("bike")}>Cycle</button>
        </div>
      </div>
      {GOOGLE_KEY && <PlaceSearch onPick={(ll) => (picking === "to" ? setTo(ll) : setFrom(ll))} />}
      <div className="grid gap-2">
        <button className={`field ${picking === "from" ? "active" : ""}`} onClick={() => setPicking(picking === "from" ? null : "from")}>
          <i className="pin" style={{ background: "var(--accent)" }} />
          <span className="min-w-0 truncate">{picking === "from" ? "Click the map…" : `From: ${place(from, labels.from, "set origin")}`}</span>
        </button>
        <button className={`field ${picking === "to" ? "active" : ""}`} onClick={() => setPicking(picking === "to" ? null : "to")}>
          <i className="pin" style={{ background: "#c0392f" }} />
          <span className="min-w-0 truncate">{picking === "to" ? "Click the map…" : `To: ${place(to, labels.to, "set destination")}`}</span>
        </button>
      </div>

      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {busy && !res && <div className="card h-40 animate-pulse" />}
      {res && main && (
        <section className="reco" aria-label="Recommended route">
          <div className="eyebrow" style={{ color: "var(--accent)" }}>{noBetter ? "No clearly cleaner route here" : "Cleanest route"}</div>
          <div className="big">{noBetter ? "Take the fastest, or the one you prefer" : gain > 0 ? `${gain}% less polluted air` : "Same air as the fastest route"}</div>
          {noBetter && <p className="muted m-0 text-[13px]">{res.confidence?.message}</p>}
          <div className="grid grid-cols-2 gap-2">
            <div className="stat"><b className="tabular-nums">{Math.round(main.time_min)} min</b><span>{main.time_change_pct ? `+${main.time_change_pct}% vs fastest` : "same as fastest"}</span></div>
            <div className="stat"><b className="tabular-nums">{main.mean_pm25} µg/m³</b><span>average PM2.5 on the route</span></div>
          </div>
          <div className="flex flex-wrap gap-2">
            {(!res.confidence || res.confidence.cleaner_exists) && <a className="btn btn-primary" href={res.open_in_google_maps_clean} target="_blank" rel="noreferrer">Open cleanest in Google Maps ↗</a>}
            <a className="btn" href={res.open_in_google_maps} target="_blank" rel="noreferrer">Open direct ↗</a>
          </div>
          <p className="faint m-0 text-[11px]">Exposure is modelled PM2.5 × minutes. The cleanest route adds at most 30 % to the travel time. Cycling assumes 2× the walking breathing rate. Routes follow OpenStreetMap pedestrian and cycling data; times assume 4.8 km/h walking and 15 km/h cycling. No location is stored. Indicative.</p>
        </section>
      )}
    </div>
  );
}
