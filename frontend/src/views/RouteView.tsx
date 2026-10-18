import { useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { Mode, RouteResp, RouteStats, Scenario } from "../types";
import type { Stage } from "../stage";
import { useHourlyPreload } from "../forecast";
import { api2 } from "../api";
import { PRESET_PAIRS, GOOGLE_KEY } from "../map/config";
import { markerLayer, routeLayers, ROUTE_COLORS } from "../map/layers";
import PlaceSearch from "../components/PlaceSearch";

type LL = [number, number]; // lat, lng
interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

// One route, tuned for the cleanest air within the time cap the router allows: the only choice is how you travel.
const LAMBDA = 3;

type Name = "fastest" | "balanced" | "cleanest";
const NAMES: Record<Name, string> = { fastest: "Fastest", balanced: "Balanced", cleanest: "Cleanest" };
const css = (n: Name) => `rgb(${ROUTE_COLORS[n].join(",")})`;
const ll = (p: [number, number]) => `${p[1].toFixed(6)},${p[0].toFixed(6)}`; // polylines are [lon, lat], Google wants lat,lng

/** A Google Maps walking link through a handful of points along our own route, so Google follows the same streets. */
function mapsVia(res: RouteResp, line: [number, number][], mode: Mode) {
  const picks = Array.from({ length: 8 }, (_, i) => line[Math.round(((i + 1) * (line.length - 1)) / 9)]).filter(Boolean);
  return `https://www.google.com/maps/dir/?api=1&travelmode=${mode === "bike" ? "bicycling" : "walking"}&origin=${res.from.join(",")}&destination=${res.to.join(",")}&waypoints=${picks.map(ll).join("%7C")}`;
}

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

  // fastest first, then the alternatives that are really different routes (the router often returns the same street sequence for all three)
  const options = useMemo(() => {
    if (!res) return [] as { name: Name; r: RouteStats; line: [number, number][]; link: string }[];
    const out: { name: Name; r: RouteStats; line: [number, number][]; link: string }[] = [];
    for (const name of ["fastest", "balanced", "cleanest"] as Name[]) {
      const r = res.routes[name];
      // "the same route" means the same to the precision shown: under half a minute, half a percent of exposure and one percent of length apart
      if (!r || out.some((o) => Math.abs(o.r.time_min - r.time_min) < 0.5 && Math.abs(o.r.dose_change_pct - r.dose_change_pct) < 0.5 && Math.abs(o.r.distance_m - r.distance_m) < 0.01 * o.r.distance_m)) continue;
      const line = name === "cleanest" && res.snapped.used ? res.snapped.polyline : r.polyline;
      const link = name === "fastest" ? res.open_in_google_maps : name === "cleanest" ? res.open_in_google_maps_clean : mapsVia(res, line, mode);
      out.push({ name, r, line, link });
    }
    return out;
  }, [res, mode]);
  const [choice, setChoice] = useState<Name | null>(null);
  useEffect(() => setChoice(null), [routeKey]);
  const recommended = options.find((o) => o.name === "cleanest") ?? options[0];
  const selected = options.find((o) => o.name === choice) ?? recommended;

  const layers = useMemo(() => {
    const lines = options.map((o) => ({ name: o.name, line: o.line, width: o === selected ? 8 : 4 }));
    const pts = [];
    if (from) pts.push({ id: "a", lonlat: [from[1], from[0]] as [number, number], label: "A", color: [31, 111, 92] as [number, number, number] });
    if (to) pts.push({ id: "b", lonlat: [to[1], to[0]] as [number, number], label: "B", color: [192, 57, 43] as [number, number, number] });
    return [...routeLayers(lines), ...markerLayer(pts)];
  }, [options, selected, from, to]);

  useEffect(() => {
    set({
      layers, base: "dim", cursor: picking ? "crosshair" : undefined,
      legend: { kind: "route", title: "Routes", bar: <div className="mt-1.5 grid gap-1">{(["cleanest", "balanced", "fastest"] as Name[]).map((n) => <span key={n} className="flex items-center gap-2"><i className="inline-block h-1 w-5 rounded" style={{ background: css(n) }} />{NAMES[n]}</span>)}</div> },
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

  const fastest = res?.routes.fastest ?? null;
  const same = !!res && options.length === 1; // every alternative is the same street sequence as the fastest
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
      {res && selected && fastest && (
        <section className="reco" aria-label="Route options">
          <div className="eyebrow" style={{ color: "var(--accent)" }}>{same ? "All routes are the same" : "Your options"}</div>
          <div className="big">
            {same ? "No cleaner way here"
              : selected.name === "fastest" ? "The fastest route"
              : selected.r.dose_change_pct < 0 ? `${Math.abs(Math.round(selected.r.dose_change_pct))}% less polluted air` : "About the same air"}
            {!same && selected.name !== "fastest" && <span className="muted text-base font-semibold">{` · ${Math.round(selected.r.time_min - fastest.time_min) > 0 ? `+${Math.round(selected.r.time_min - fastest.time_min)} min` : "same time"}`}</span>}
          </div>
          {same && <p className="muted m-0 text-[13px]">{res.confidence?.message ?? "The cleaner alternatives follow the same streets as the fastest route."} The air is similar along every street between these two places, so a detour would only add time.</p>}
          <table className="w-full text-[13px]">
            <thead>
              <tr className="faint text-left"><th className="py-1 font-normal">Route</th><th className="font-normal">Time</th><th className="font-normal">Pollution exposure</th><th className="font-normal">PM2.5</th></tr>
            </thead>
            <tbody>
              {options.map((o) => {
                const extra = Math.round(o.r.time_min - fastest.time_min), dose = Math.round(o.r.dose_change_pct);
                return (
                  <tr key={o.name} onClick={() => setChoice(o.name)} className="cursor-pointer border-t" style={{ borderColor: "var(--line)", background: o === selected ? "var(--surface-2)" : undefined }} aria-selected={o === selected}>
                    <td className="py-2"><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: css(o.name) }} /><b>{NAMES[o.name]}</b></td>
                    <td className="tabular-nums">{Math.round(o.r.time_min)} min<span className="faint"> {extra > 0 ? `(+${extra})` : ""}</span></td>
                    <td className="tabular-nums"><b style={{ color: dose < 0 ? "var(--good)" : undefined }}>{dose === 0 ? "baseline" : `${dose > 0 ? "+" : ""}${dose}%`}</b></td>
                    <td className="tabular-nums">{o.r.mean_pm25} µg/m³</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!same && res.confidence && !res.confidence.cleaner_exists && <p className="muted m-0 text-xs">{res.confidence.message}</p>}
          <div className="flex flex-wrap gap-2">
            <a className="btn btn-primary" href={selected.link} target="_blank" rel="noreferrer">{same ? "Open in Google Maps ↗" : `Open ${NAMES[selected.name].toLowerCase()} in Google Maps ↗`}</a>
            {selected.name !== "fastest" && <a className="btn" href={res.open_in_google_maps} target="_blank" rel="noreferrer">Open fastest ↗</a>}
          </div>
          <p className="faint m-0 text-[11px]">Exposure is modelled PM2.5 × minutes compared with the fastest route; cycling assumes 2× the walking breathing rate. Balanced and cleanest add at most 30 % to the travel time. Routes follow OpenStreetMap data; times assume 4.8 km/h walking and 15 km/h cycling. No location is stored. Indicative.</p>
        </section>
      )}
    </div>
  );
}
