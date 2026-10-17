import { useEffect, useMemo, useRef, useState } from "react";
import type { PickingInfo } from "@deck.gl/core";
import type { RouteResp, RouteStats, Scenario } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { PRESET_PAIRS, MAP_PROVIDER } from "../map/config";
import { markerLayer, routeLayers, ROUTE_COLORS } from "../map/layers";
import PlaceSearch from "../components/PlaceSearch";

type LL = [number, number]; // lat, lng
interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

const NAMES: Record<string, string> = { fastest: "Fastest", balanced: "Balanced", cleanest: "Cleanest", chosen: "Your setting" };
const css = (n: string) => `rgb(${ROUTE_COLORS[n].join(",")})`;

export default function RouteView({ stage, scenario, hourIndex }: Props) {
  const [from, setFrom] = useState<LL | null>(PRESET_PAIRS[1].from as unknown as LL);
  const [to, setTo] = useState<LL | null>(PRESET_PAIRS[1].to as unknown as LL);
  const [picking, setPicking] = useState<"from" | "to" | null>(null);
  const [lam, setLam] = useState(2);
  const [res, setRes] = useState<RouteResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState<string>("cleanest");
  const [labels, setLabels] = useState<{ from: string; to: string }>({ from: PRESET_PAIRS[1].label.split(" → ")[0].split(": ").pop() ?? "", to: PRESET_PAIRS[1].label.split(" → ")[1] ?? "" });
  const pickRef = useRef(picking);
  pickRef.current = picking;
  const { set } = stage;

  useEffect(() => {
    if (!from || !to) return;
    let off = false;
    setBusy(true);
    const t = setTimeout(() => {
      api2.route(from, to, lam, scenario, hourIndex)
        .then((r) => { if (!off) { setRes(r); setErr(null); setLabels({ from: r.from_label, to: r.to_label }); } })
        .catch((e: Error) => { if (!off) { setErr(e.message); setRes(null); } })
        .finally(() => !off && setBusy(false));
    }, 220);
    return () => { off = true; clearTimeout(t); };
  }, [from, to, lam, scenario, hourIndex]);

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

  return (
    <div className="grid gap-3">
      <div className="card grid gap-3 p-4">
        <div className="text-sm font-semibold">Clean-air walking route <span className="faint font-normal">· indicative</span></div>
        {MAP_PROVIDER === "google" && <PlaceSearch onPick={(ll) => (picking === "to" ? setTo(ll) : setFrom(ll))} />}
        <div className="flex flex-wrap gap-2">
          <button className={`btn ${picking === "from" ? "btn-primary" : ""}`} onClick={() => setPicking(picking === "from" ? null : "from")}>
            {picking === "from" ? "Click the map…" : `A: ${from ? labels.from || from.map((x) => x.toFixed(4)).join(", ") : "set origin"}`}
          </button>
          <button className={`btn ${picking === "to" ? "btn-primary" : ""}`} onClick={() => setPicking(picking === "to" ? null : "to")}>
            {picking === "to" ? "Click the map…" : `B: ${to ? labels.to || to.map((x) => x.toFixed(4)).join(", ") : "set destination"}`}
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {PRESET_PAIRS.map((p) => (
            <button key={p.id} className="badge cursor-pointer" onClick={() => { setFrom(p.from as unknown as LL); setTo(p.to as unknown as LL); const [a, b] = p.label.split(" → "); setLabels({ from: a.split(": ").pop() ?? "", to: b ?? "" }); }}>{p.label}</button>
          ))}
        </div>
        <div>
          <div className="flex justify-between text-xs"><span className="font-semibold">Faster</span><span className="muted">setting {lam.toFixed(1)}</span><span className="font-semibold">Cleaner</span></div>
          <input type="range" min={0} max={3} step={0.1} value={lam} onChange={(e) => setLam(Number(e.target.value))} aria-label="Faster to cleaner" />
        </div>
      </div>

      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {busy && !res && <div className="card h-40 animate-pulse" />}
      {res && (
        <div className="card p-3">
          <table className="w-full text-xs">
            <thead>
              <tr className="faint text-left"><th className="py-1 font-normal">Route</th><th className="font-normal">Time</th><th className="font-normal">Dose*</th><th className="font-normal">Mean PM2.5</th><th className="font-normal">CAI</th></tr>
            </thead>
            <tbody>
              {rows.filter(([, r]) => r).map(([n, r]) => r && (
                <tr key={n} onClick={() => setFocus(n)} className="cursor-pointer border-t" style={{ borderColor: "var(--line)", background: focus === n ? "var(--surface-2)" : undefined }}>
                  <td className="py-1.5"><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: css(n) }} /><b>{NAMES[n]}</b></td>
                  <td className="tabular-nums">{Math.round(r.time_min)} min<span className="faint"> {r.time_change_pct ? `(+${r.time_change_pct}%)` : ""}</span></td>
                  <td className="tabular-nums"><b style={{ color: r.dose_change_pct < 0 ? "var(--good)" : undefined }}>{r.dose_change_pct === 0 ? "base" : `${r.dose_change_pct}%`}</b></td>
                  <td className="tabular-nums">{r.mean_pm25}</td>
                  <td className="tabular-nums">{r.mean_cai != null ? Math.round(r.mean_cai) : "–"}</td>
                </tr>
              ))}
              {res.google_baseline && (
                <tr className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="py-1.5"><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm border border-dashed" style={{ borderColor: "var(--ink-2)" }} /><b>Google</b></td>
                  <td className="tabular-nums">{Math.round(res.google_baseline.time_min)} min</td>
                  <td className="tabular-nums"><b>{res.google_baseline.dose_change_pct}%</b></td>
                  <td className="tabular-nums">{res.google_baseline.mean_pm25}</td>
                  <td className="tabular-nums">{res.google_baseline.mean_cai != null ? Math.round(res.google_baseline.mean_cai) : "–"}</td>
                </tr>
              )}
            </tbody>
          </table>
          <p className="faint mt-2 text-[11px] leading-snug">
            *Dose = modelled PM2.5 × minutes walked (µg·min/m³), % vs the fastest route. Balanced and cleanest are capped at +30 % time.
            {res.routes.cleanest.lambda_used !== undefined && res.routes.cleanest.lambda_used < 3 ? ` The cap limited the cleanest route to setting ${res.routes.cleanest.lambda_used}.` : ""}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <a className="btn text-xs" href={res.open_in_google_maps_clean} target="_blank" rel="noreferrer">Open cleanest in Google Maps ↗</a>
            <a className="btn text-xs" href={res.open_in_google_maps} target="_blank" rel="noreferrer">Open direct ↗</a>
          </div>
          <p className="faint mt-2 text-[11px]">
            {res.google_status === "ok"
              ? res.snapped.used ? `Cleanest route shown is Google's walking route through ${res.snapped.waypoints.length} of our waypoints (ETA ${res.snapped.eta_min} min).` : "Google snapped route unavailable; showing our own polyline."
              : res.google_status === "no_key" ? "Google baseline needs GOOGLE_ROUTES_SERVER_KEY (see README); routing and CAI never depend on it." : "Google Routes request failed; showing our own routes."}
          </p>
        </div>
      )}
      <p className="faint text-[11px]">Routes follow OpenStreetMap pedestrian data (some crossings or private paths may be missing). Times assume 4.8 km/h. No location is stored.</p>
    </div>
  );
}
