import { PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import type { Layer } from "@deck.gl/core";
import type { Sensor, Chokepoint, NetworkResp, RefStation, RouteStats, TrafficResp, TrafficCorridor, IncidentsResp, Incident } from "../types";
import { buildAttributes, caiColor, relColor, relRange, type Geometry } from "./cai";

type RGB = [number, number, number];
type Seg = NetworkResp["segments"][number];
type Trip = { path: [number, number][]; ts: number[] };
type Hl = { path: [number, number][]; sel: boolean };
export const ROUTE_COLORS: Record<string, RGB> = {
  fastest: [74, 85, 99], balanced: [224, 150, 30], cleanest: [31, 138, 112], chosen: [37, 99, 235], google: [90, 90, 90], snapped: [31, 138, 112],
};

/** The street-level CAI: one PathLayer over ~119k segments from binary arrays. */
export function caiLayer(g: Geometry, cai: ArrayLike<number>, relative: boolean, mode: "full" | "dim"): Layer {
  const { colors, widths } = buildAttributes(g, cai, relative, mode === "dim" ? 70 : 235);
  return new PathLayer({
    id: "cai",
    data: { length: g.n, startIndices: g.offsets, attributes: { getPath: { value: g.coords, size: 2 }, getColor: { value: colors, size: 4 }, getWidth: { value: widths, size: 1 } } },
    _pathType: "open", widthUnits: "meters", widthMinPixels: 1.2, widthMaxPixels: 9, capRounded: true, jointRounded: true,
    pickable: mode === "full", autoHighlight: mode === "full", highlightColor: [255, 255, 255, 220],
  } as never);
}

const pmColor = (v: number): [number, number, number, number] =>
  v < 15 ? [47, 158, 99, 255] : v < 35 ? [224, 176, 48, 255] : v < 55 ? [232, 116, 44, 255] : v < 150 ? [192, 57, 43, 255] : [122, 58, 154, 255];

/** White outline around the street segment whose breakdown is shown. */
export function highlightLayer(path: [number, number][]): Layer {
  return new PathLayer<{ path: [number, number][] }>({
    id: "selected-segment", data: [{ path }], getPath: (d: { path: [number, number][] }) => d.path, getColor: [255, 255, 255, 255] as never,
    widthUnits: "pixels", getWidth: 9, capRounded: true, jointRounded: true,
  } as never);
}

export function sensorLayer(sensors: Sensor[]): Layer[] {
  const isAt = (s: Sensor) => s.source === "airthings";
  return [
    new ScatterplotLayer<Sensor>({
      id: "sensors", data: sensors.filter((s) => !isAt(s)), getPosition: (s) => [s.lon, s.lat], getFillColor: (s) => pmColor(s.pm25),
      radiusUnits: "pixels", getRadius: 4, stroked: true, getLineColor: [255, 255, 255, 255], lineWidthUnits: "pixels", getLineWidth: 1, pickable: false,
    }),
    // Sofia's Airthings pilot stations: larger, dark-blue ring, hoverable (name + hourly value)
    new ScatterplotLayer<Sensor>({
      id: "airthings-sensors", data: sensors.filter(isAt), getPosition: (s) => [s.lon, s.lat], getFillColor: (s) => pmColor(s.pm25),
      radiusUnits: "pixels", getRadius: 6.5, stroked: true, getLineColor: [29, 78, 216, 255], lineWidthUnits: "pixels", getLineWidth: 2.5, pickable: true,
    }),
  ];
}

/** Municipal reference stations: big ringed dots with a name label (citizen sensors are small plain dots). */
export function referenceLayers(stations: RefStation[]): Layer[] {
  return [
    new ScatterplotLayer<RefStation>({
      id: "reference-stations", data: stations, getPosition: (s: RefStation) => [s.lon, s.lat], radiusUnits: "pixels", getRadius: 9,
      getFillColor: [255, 255, 255, 255], stroked: true, getLineColor: [17, 24, 39, 255], lineWidthUnits: "pixels", getLineWidth: 3,
    }),
    new TextLayer<RefStation>({
      id: "reference-labels", data: stations, getPosition: (s: RefStation) => [s.lon, s.lat],
      getText: (s: RefStation) => (s.pm10 != null ? `${s.name} · PM10 ${Math.round(s.pm10)}` : s.name), getSize: 12, getColor: [17, 24, 39, 255],
      fontWeight: 700, getTextAnchor: "start", getAlignmentBaseline: "center", getPixelOffset: [14, 0], background: true,
      getBackgroundColor: [255, 255, 255, 235], backgroundPadding: [4, 2, 4, 2],
    } as never),
  ];
}

export function routeLayers(r: { name: string; line: [number, number][]; width?: number; dashed?: boolean }[]): Layer[] {
  return r.map(({ name, line, width = 5, dashed }) => {
    const color = ROUTE_COLORS[name] ?? [60, 60, 60];
    return new PathLayer<{ path: [number, number][] }>({
      id: `route-${name}`, data: [{ path: line }], getPath: (d: { path: [number, number][] }) => d.path, getColor: [...color, dashed ? 200 : 235] as never,
      widthUnits: "pixels", getWidth: width, capRounded: true, jointRounded: true,
      ...(dashed ? { getDashArray: [4, 3], dashJustified: true, extensions: [new PathStyleExtension({ dash: true })] } : {}),
    } as never);
  });
}

export function markerLayer(points: { id: string; lonlat: [number, number]; label: string; color: RGB }[]): Layer[] {
  return [
    new ScatterplotLayer({
      id: "od-dots", data: points, getPosition: (d: (typeof points)[number]) => d.lonlat, getFillColor: (d: (typeof points)[number]) => [...d.color, 255] as never,
      radiusUnits: "pixels", getRadius: 9, stroked: true, getLineColor: [255, 255, 255, 255], lineWidthUnits: "pixels", getLineWidth: 3,
    }),
    new TextLayer({
      id: "od-labels", data: points, getPosition: (d: (typeof points)[number]) => d.lonlat, getText: (d: (typeof points)[number]) => d.label,
      getSize: 12, getColor: [255, 255, 255, 255], fontWeight: 700, getTextAnchor: "middle", getAlignmentBaseline: "center",
    }),
  ];
}

// ---- network view ---------------------------------------------------------------------------------------
export function networkStatic(n: NetworkResp, relative: boolean, selected: Chokepoint | null): Layer[] {
  const maxUse = Math.max(1, ...n.segments.map((s) => s.usage_clean));
  const { lo, hi } = relRange(n.segments.map((s) => s.cai));
  const col = (v: number) => (relative ? relColor(v, lo, hi) : caiColor(v));
  const cps = n.chokepoints;
  return [
    new PathLayer<Seg>({
      id: "net-segments", data: n.segments, getPath: (d: Seg) => d.path, getColor: (d: Seg) => [...col(d.cai), 215] as never,
      getWidth: (d: Seg) => 1.5 + 9 * Math.sqrt(d.usage_clean / maxUse), widthUnits: "pixels", capRounded: true, jointRounded: true, pickable: false,
    } as never),
    new PathLayer<Hl>({
      id: "net-choke", data: cps.flatMap((c) => c.segments.map((p) => ({ path: p, sel: selected?.rank === c.rank }))),
      getPath: (d: Hl) => d.path, getColor: (d: Hl) => (d.sel ? [255, 255, 255, 255] : [214, 40, 140, 235]) as never,
      getWidth: (d: Hl) => (d.sel ? 8 : 5), widthUnits: "pixels", capRounded: true, jointRounded: true,
      updateTriggers: { getColor: selected?.rank, getWidth: selected?.rank },
    } as never),
    new ScatterplotLayer<Chokepoint>({
      id: "net-choke-dot", data: cps, getPosition: (c: Chokepoint) => [c.lon, c.lat], radiusUnits: "pixels", getRadius: 11,
      getFillColor: (c: Chokepoint) => (selected?.rank === c.rank ? [255, 255, 255, 255] : [214, 40, 140, 255]) as never,
      stroked: true, getLineColor: [255, 255, 255, 255], lineWidthUnits: "pixels", getLineWidth: 2, pickable: true,
      updateTriggers: { getFillColor: selected?.rank },
    }),
    new TextLayer<Chokepoint>({
      id: "net-choke-rank", data: cps, getPosition: (c: Chokepoint) => [c.lon, c.lat], getText: (c: Chokepoint) => String(c.rank), getSize: 12,
      getColor: (c: Chokepoint) => (selected?.rank === c.rank ? [214, 40, 140, 255] : [255, 255, 255, 255]) as never, fontWeight: 700,
      getTextAnchor: "middle", getAlignmentBaseline: "center", updateTriggers: { getColor: selected?.rank },
    }),
  ];
}

/** Pulses along the busiest clean corridors (timestamps = cumulative distance fraction, staggered per trip). */
export function flowLayer(n: NetworkResp, time: number): Layer {
  return new TripsLayer<Trip>({
    id: "net-flow", data: tripsData(n.trips), getPath: (d: Trip) => d.path, getTimestamps: (d: Trip) => d.ts, getColor: [255, 255, 255, 255] as never,
    widthUnits: "pixels", getWidth: 4, trailLength: 0.22, currentTime: time % 1.2, fadeTrail: true, capRounded: true, jointRounded: true,
  } as never);
}

const tripCache = new WeakMap<object, { path: [number, number][]; ts: number[] }[]>();
function tripsData(trips: [number, number][][]) {
  let c = tripCache.get(trips);
  if (!c) {
    c = trips.map((path, i) => {
      const d = [0];
      for (let k = 1; k < path.length; k++) d.push(d[k - 1] + Math.hypot(path[k][0] - path[k - 1][0], (path[k][1] - path[k - 1][1]) * 1.35));
      const total = d[d.length - 1] || 1;
      const offset = (i * 0.173) % 1; // stagger so the pulses do not all start together
      return { path, ts: d.map((x) => offset + (x / total) * 0.9) };
    });
    tripCache.set(trips, c);
  }
  return c;
}

export const fmtDose = (r: RouteStats) => `${Math.round(r.dose)} µg·min/m³`;

// ---- areas (air / heat) and clean channels ---------------------------------------------------------------
import { GridCellLayer } from "@deck.gl/layers";
import type { AreasResp } from "../types";

export interface AreaRow { lon: number; lat: number; mean: number; min: number; max: number; street_m: number; n: number; pm25?: number; anomaly?: number }

export function areaRows(a: AreasResp): AreaRow[] {
  const c = a.cells;
  return c.lon.map((lon, i) => ({ lon, lat: c.lat[i], mean: c.mean[i], min: c.min[i], max: c.max[i], street_m: c.street_m[i], n: c.n[i], pm25: c.pm25?.[i], anomaly: c.anomaly?.[i] }));
}

/** Diverging cool-to-hot palette (ColorBrewer RdBu, colour-blind safe) centred on the city median. */
export function heatColor(anomaly: number, range: number): [number, number, number] {
  const t = Math.max(-1, Math.min(1, anomaly / Math.max(0.1, range)));
  const cool: RGB = [33, 102, 172], mid: RGB = [247, 247, 247], hot: RGB = [178, 24, 43];
  const [a, b, u] = t < 0 ? [mid, cool, -t] : [mid, hot, t];
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * u)) as RGB;
}

export function areaLayer(a: AreasResp, rows: AreaRow[], scale: "relative" | "absolute"): Layer {
  const { lo, hi } = relRange(rows.map((r) => r.mean));
  const heat = a.metric === "heat";
  const range = Math.max(Math.abs(a.thresholds.cool ?? 0), Math.abs(a.thresholds.hot ?? 0), 1);
  const extreme = (r: AreaRow) => (heat ? (r.anomaly ?? 0) >= (a.thresholds.hot ?? 99) || (r.anomaly ?? 0) <= (a.thresholds.cool ?? -99)
    : r.mean >= (a.thresholds.clean ?? 101) || r.mean <= (a.thresholds.dirty ?? -1));
  return new GridCellLayer<AreaRow>({
    id: "areas", data: rows, getPosition: (r: AreaRow) => [r.lon, r.lat], cellSize: a.cell_m, coverage: 0.96, extruded: false, material: false,
    getFillColor: (r: AreaRow) => {
      const c = heat ? heatColor(r.anomaly ?? 0, range)
        : scale === "relative" ? relColor(r.mean, lo, hi) : caiColor(r.mean);
      return [...c, extreme(r) ? 215 : 120] as never; // the cleanest/dirtiest (or coolest/hottest) 10 % stand out
    },
    pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 90],
    updateTriggers: { getFillColor: [a.metric, a.time, scale, a.mode] },
  } as never);
}

const CHANNEL: RGB = [0, 105, 92];

export function segmentLayers(n: NetworkResp, opts: { channels: boolean; other: boolean }): Layer[] {
  const maxUse = Math.max(1, ...n.segments.map((s) => s.usage_clean));
  const w = (s: Seg) => 2 + 7 * Math.sqrt(s.usage_clean / maxUse);
  const out: Layer[] = [];
  if (opts.other) {
    out.push(new PathLayer<Seg>({
      id: "net-other", data: n.segments.filter((s) => !s.channel), getPath: (d: Seg) => d.path, getColor: [70, 80, 95, 110] as never,
      getWidth: 1.5, widthUnits: "pixels", capRounded: true, jointRounded: true,
    } as never));
  }
  if (opts.channels) {
    const ch = n.segments.filter((s) => s.channel);
    out.push(
      new PathLayer<Seg>({ id: "net-channel-casing", data: ch, getPath: (d: Seg) => d.path, getColor: [255, 255, 255, 235] as never,
        getWidth: (d: Seg) => w(d) + 4, widthUnits: "pixels", capRounded: true, jointRounded: true } as never),
      new PathLayer<Seg>({ id: "net-channel", data: ch, getPath: (d: Seg) => d.path, getColor: [...CHANNEL, 255] as never,
        getWidth: w, widthUnits: "pixels", capRounded: true, jointRounded: true, pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 200] } as never),
    );
  }
  return out;
}

export function chokepointLayers(n: NetworkResp, selected: Chokepoint | null): Layer[] {
  return networkStatic(n, false, selected).slice(1); // the chokepoint highlight, dots and rank labels from the original builder
}

// ---- real traffic: arterial corridors (live vs typical) and incidents ---------------------------------------------
/** Live travel time relative to typical for this hour: bluer = lighter than usual, red = much heavier. */
export function trafficColor(r: number | null): RGB {
  if (r == null) return [140, 148, 160];
  return r < 0.95 ? [59, 130, 246] : r < 1.05 ? [120, 130, 150] : r < 1.2 ? [240, 170, 40] : r < 1.4 ? [226, 100, 40] : [190, 30, 40];
}
export const trafficWord = (r: number | null) =>
  r == null ? "no live reading" : r < 0.95 ? "lighter than usual" : r < 1.05 ? "about usual" : r < 1.2 ? "a little heavier than usual" : r < 1.4 ? "heavy for this hour" : "very heavy for this hour";

const MAG_COLOR: RGB[] = [[120, 120, 120], [240, 190, 40], [232, 116, 44], [192, 40, 40], [20, 20, 20]];
const incidentColor = (i: Incident): RGB => MAG_COLOR[Math.min(i.properties.magnitude_code, 4)] ?? MAG_COLOR[0];
const incidentPath = (i: Incident): [number, number][] => (i.geometry.type === "LineString" ? i.geometry.coordinates : [i.geometry.coordinates]);

export function trafficLayers(t: TrafficResp | null, inc: IncidentsResp | null): Layer[] {
  const out: Layer[] = [];
  const cs = (t?.corridors ?? []).filter((c) => c.polyline.length > 1);
  if (cs.length) {
    out.push(
      new PathLayer<TrafficCorridor>({ id: "traffic-casing", data: cs, getPath: (c: TrafficCorridor) => c.polyline, getColor: [255, 255, 255, 235] as never,
        getWidth: 9, widthUnits: "pixels", capRounded: true, jointRounded: true } as never),
      new PathLayer<TrafficCorridor>({ id: "traffic-corridors", data: cs, getPath: (c: TrafficCorridor) => c.polyline,
        getColor: (c: TrafficCorridor) => [...trafficColor(c.vs_typical), 255] as never, getWidth: 5, widthUnits: "pixels", capRounded: true, jointRounded: true,
        pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 120] } as never),
    );
  }
  const feats = inc?.features ?? [];
  if (feats.length) {
    out.push(
      new PathLayer<Incident>({ id: "incident-lines", data: feats.filter((f) => f.geometry.type === "LineString"), getPath: (f: Incident) => incidentPath(f),
        getColor: (f: Incident) => [...incidentColor(f), 255] as never, getWidth: 6, widthUnits: "pixels", capRounded: true, jointRounded: true, pickable: true } as never),
      new ScatterplotLayer<Incident>({ id: "incident-dots", data: feats, getPosition: (f: Incident) => incidentPath(f)[Math.floor(incidentPath(f).length / 2)],
        getFillColor: (f: Incident) => [...incidentColor(f), 255] as never, radiusUnits: "pixels", getRadius: 6, stroked: true, getLineColor: [255, 255, 255, 255],
        lineWidthUnits: "pixels", getLineWidth: 2, pickable: true }),
    );
  }
  return out;
}

const esc = (x: string) => x.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export function trafficTooltip(info: { layer?: { id: string } | null; object?: unknown }): { html: string } | null {
  const id = info.layer?.id;
  if (id === "traffic-corridors") {
    const c = info.object as TrafficCorridor;
    const g = c.google.duration_s ? `Google ${Math.round(c.google.duration_s / 60)} min` : "";
    const t = c.tomtom.tti ? `TomTom flow ×${c.tomtom.tti.toFixed(2)} of free-flow time` : "";
    return { html: `<b>${esc(c.name)}</b><br>${trafficWord(c.vs_typical)}${c.vs_typical ? ` (×${c.vs_typical.toFixed(2)} of typical)` : ""}<br>${[g, t].filter(Boolean).join(" · ")}` };
  }
  if (id === "airthings-sensors") {
    const a = info.object as Sensor;
    const when = a.timestamp ? ` · hour starting ${a.timestamp.slice(11, 16)} UTC` : "";
    return { html: `<b>${esc(a.name ?? "Airthings station")}</b><br>PM2.5 ${a.pm25} µg/m³${a.pm10 != null ? ` · PM10 ${a.pm10}` : ""}<br>Airthings pilot network${when}` };
  }
  if (id === "incident-lines" || id === "incident-dots") {
    const p = (info.object as Incident).properties;
    const where = [p.from, p.to].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" → ");
    return { html: `<b>${esc(p.category)}</b> · ${esc(p.magnitude)}<br>${esc(where)}${p.length_m ? ` · ${p.length_m} m` : ""}${p.delay_s ? `<br>delay ${Math.round(p.delay_s / 60)} min` : ""}${p.description ? `<br>${esc(p.description)}` : ""}` };
  }
  return null;
}
