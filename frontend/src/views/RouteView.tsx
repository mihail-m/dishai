import { useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { Mode, RouteResp, RouteStats, Scenario } from "../types";
import type { Stage } from "../stage";
import { useHourlyPreload } from "../forecast";
import { api2 } from "../api";
import { PRESET_PAIRS, GOOGLE_KEY } from "../map/config";
import { markerLayer, routeLayers, ROUTE_COLORS } from "../map/layers";
import PlaceSearch from "../components/PlaceSearch";
import { Acc } from "../components/ui";

type LL = [number, number]; // lat, lng
interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

const NAMES: Record<string, string> = { fastest: "Fastest", balanced: "Balanced", cleanest: "Cleanest", chosen: "Your setting" };
const css = (n: string) => `rgb(${ROUTE_COLORS[n].join(",")})`;

export default function RouteView({ stage, scenario, hourIndex }: Props) {
  const [from, setFrom] = useState<LL | null>(PRESET_PAIRS[1].from as unknown as LL);
  const [to, setTo] = useState<LL | null>(PRESET_PAIRS[1].to as unknown as LL);
  const [picking, setPicking] = useState<"from" | "to" | null>(null);
  const [lam, setLam] = useState(2);
  const [mode, setMode] = useState<Mode>("walk");
  const [googleResult, setGoogleResult] = useState<{ key: string; value: RouteResp } | null>(null);
  const [focus, setFocus] = useState<string>("cleanest");
  const [labels, setLabels] = useState<{ from: string; to: string }>({ from: PRESET_PAIRS[1].label.split(" → ")[0].split(": ").pop() ?? "", to: PRESET_PAIRS[1].label.split(" → ")[1] ?? "" });
  const pickRef = useRef(picking);
  pickRef.current = picking;
  const { set } = stage;

  const window = stage.forecast;
  const routeKey = window && from && to ? `${window.id}:route:${mode}:${from.join()}:${to.join()}:${lam}` : null;
  const history = useHourlyPreload<RouteResp>(routeKey, window?.hours ?? [], hourIndex,
    (hour, signal) => api2.route(from!, to!, lam, scenario, hour, mode, { forecastId: window!.id, signal, includeGoogle: false }), 220);
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
    api2.route(from, to, lam, scenario, hourIndex, mode, { forecastId: window.id, signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setGoogleResult({ key: googleKey, value }); })
      .catch(() => { if (!controller.signal.aborted) setGoogleResult({ key: googleKey, value: { ...ownRoute, google_status: "error" } }); });
    return () => controller.abort();
  }, [googleKey, history.value?.google_available]);

  const layers = useMemo(() => {
    const lines: { name: string; line: [number, number][]; width?: number; dashed?: boolean }[] = [];
    if (res) {
      const r = res.routes;
      const w = (n: string) => (focus === n ? 8 : 4);
      lines.push({ name: "fastest", line: r.fastest.polyline, width: w("fastest") });
      lines.push({ name: "balanced", line: r.balanced.polyline, width: w("balanced") });
      lines.push({ name: "cleanest", line: res.snapped.used ? res.snapped.polyline : r.cleanest.polyline, width: w("cleanest") });
      if (r.chosen) lines.push({ name: "chosen", line: r.chosen.polyline, width: w("chosen") });
      if (res.google_baseline) lines.push({ name: "google", line: res.google_baseline.polyline, width: 4, dashed: true });
    }
    const pts = [];
    if (from) pts.push({ id: "a", lonlat: [from[1], from[0]] as [number, number], label: "A", color: [31, 111, 92] as [number, number, number] });
    if (to) pts.push({ id: "b", lonlat: [to[1], to[0]] as [number, number], label: "B", color: [192, 57, 43] as [number, number, number] });
    return [...routeLayers(lines), ...markerLayer(pts)];
  }, [res, focus, from, to]);

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

  const rows: [string, RouteStats | null | undefined][] = res
    ? [["fastest", res.routes.fastest], ["balanced", res.routes.balanced], ["cleanest", res.routes.cleanest], ["chosen", res.routes.chosen]]
    : [];

  const main = res ? res.routes.chosen ?? res.routes.cleanest : null;
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
        <div className="peek-hide"><div className="flex flex-wrap gap-1.5">
          {PRESET_PAIRS.map((p) => (
            <button key={p.id} className="chip" onClick={() => { setFrom(p.from as unknown as LL); setTo(p.to as unknown as LL); const [a, b] = p.label.split(" → "); setLabels({ from: a.split(": ").pop() ?? "", to: b ?? "" }); }}>{p.label}</button>
          ))}
        </div></div>
      </div>
      <div>
        <div className="flex justify-between text-[13px]"><span className="font-semibold">Faster</span><span className="muted">setting {lam.toFixed(1)}</span><span className="font-semibold">Cleaner</span></div>
        <input type="range" min={0} max={3} step={0.1} value={lam} onChange={(e) => setLam(Number(e.target.value))} aria-label="Faster to cleaner" className="h-8" />
      </div>

      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {busy && !res && <div className="card h-40 animate-pulse" />}
      {res && main && (
        <>
          <section className="reco" aria-label="Recommended route">
            <div className="eyebrow" style={{ color: "var(--accent)" }}>{noBetter ? "No clearly cleaner route here" : "Recommended"}</div>
            <div className="big">{noBetter ? "Take the fastest, or the one you prefer" : gain > 0 ? `${gain}% less polluted air` : "Same air as the fastest route"}</div>
            {noBetter && <p className="muted m-0 text-[13px]">{res.confidence?.message}</p>}
            <div className="grid grid-cols-2 gap-2">
              <div className="stat"><b className="tabular-nums">{Math.round(main.time_min)} min</b><span>{main.time_change_pct ? `+${main.time_change_pct}% vs fastest` : "same as fastest"}</span></div>
              <div className="stat"><b className="tabular-nums">{main.mean_pm25} µg/m³</b><span>average PM2.5 on the route</span></div>
            </div>
            {res.confidence?.cleaner_exists && (
              <p className="muted m-0 text-xs">
                Clearly cleaner than the fastest: {res.confidence.better.map((k) => `${NAMES[k]} (${Math.abs(res.confidence!.routes[k].diff_pct)}% lower mean PM2.5, uncertainty ±${res.confidence!.routes[k].sd_pct}%)`).join("; ")}.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {(!res.confidence || res.confidence.cleaner_exists) && <a className="btn btn-primary" href={res.open_in_google_maps_clean} target="_blank" rel="noreferrer">Open cleanest in Google Maps ↗</a>}
              <a className="btn" href={res.open_in_google_maps} target="_blank" rel="noreferrer">Open direct ↗</a>
            </div>
          </section>
          <div>
            <Acc title="Compare all routes">
              <table className="w-full text-xs">
                <thead>
                  <tr className="faint text-left"><th className="py-1 font-normal">Route</th><th className="font-normal">Time</th><th className="font-normal">Exposure*</th><th className="font-normal">PM2.5</th><th className="font-normal">Score</th></tr>
                </thead>
                <tbody>
                  {rows.filter(([, r]) => r).map(([n, r]) => r && (
                    <tr key={n} onClick={() => setFocus(n)} className="cursor-pointer border-t" style={{ borderColor: "var(--line)", background: focus === n ? "var(--surface-2)" : undefined }}>
                      <td className="py-2"><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: css(n) }} /><b>{NAMES[n]}</b></td>
                      <td className="tabular-nums">{Math.round(r.time_min)} min<span className="faint"> {r.time_change_pct ? `(+${r.time_change_pct}%)` : ""}</span></td>
                      <td className="tabular-nums"><b style={{ color: r.dose_change_pct < 0 ? "var(--good)" : undefined }}>{r.dose_change_pct === 0 ? "base" : `${r.dose_change_pct}%`}</b></td>
                      <td className="tabular-nums">{r.mean_pm25}</td>
                      <td className="tabular-nums">{r.mean_cai != null ? Math.round(r.mean_cai) : "–"}</td>
                    </tr>
                  ))}
                  {res.google_baseline && (
                    <tr className="border-t" style={{ borderColor: "var(--line)" }}>
                      <td className="py-2"><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm border border-dashed" style={{ borderColor: "var(--ink-2)" }} /><b>Google</b></td>
                      <td className="tabular-nums">{Math.round(res.google_baseline.time_min)} min</td>
                      <td className="tabular-nums"><b>{res.google_baseline.dose_change_pct}%</b></td>
                      <td className="tabular-nums">{res.google_baseline.mean_pm25}</td>
                      <td className="tabular-nums">{res.google_baseline.mean_cai != null ? Math.round(res.google_baseline.mean_cai) : "–"}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <p className="faint m-0 text-[11px] leading-snug">
                *Exposure = modelled PM2.5 × minutes × relative breathing rate (µg·min/m³; cycling assumes 2× walking), % vs the fastest route. Balanced and cleanest are capped at +30 % time.
                {res.routes.cleanest.lambda_used !== undefined && res.routes.cleanest.lambda_used < 3 ? ` The cap limited the cleanest route to setting ${res.routes.cleanest.lambda_used}.` : ""}
              </p>
              <p className="faint m-0 text-[11px]">
                {res.google_available && googleResult?.key !== googleKey ? "Loading Google's walking comparison for this hour…"
                  : res.google_status === "n/a" ? "Google baseline is walking-only; cycling uses our own router." : res.google_status === "ok"
                  ? res.snapped.used ? `Cleanest route shown is Google's walking route through ${res.snapped.waypoints.length} of our waypoints (ETA ${res.snapped.eta_min} min).` : "Google snapped route unavailable; showing our own polyline."
                  : res.google_status === "no_key" ? "Google baseline needs GOOGLE_ROUTES_SERVER_KEY (see README); routing and the air quality score never depend on it." : "Google Routes request failed; showing our own routes."}
              </p>
            </Acc>
            <Acc title="About these routes">
              <p className="faint m-0 text-[11px]">Routes follow OpenStreetMap pedestrian data (some crossings or private paths may be missing). Times assume 4.8 km/h. No location is stored. Indicative.</p>
            </Acc>
          </div>
        </>
      )}
    </div>
  );
}
