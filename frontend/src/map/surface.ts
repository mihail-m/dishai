import { BitmapLayer, GridCellLayer, ScatterplotLayer } from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";
import type { AreaPlace, AreasResp } from "../types";
import { caiColor, relColor, relRange } from "./cai";
import { areaRows, heatColor, type AreaRow } from "./layers";

type RGB = [number, number, number];
/** A tiny version of the field (about 100 px wide) used for the ambient overlay: opacity mask, sweep position, wave phase, how polluted or hot. */
export interface Coarse { w: number; h: number; mask: Uint8Array; u: Float32Array; sinPh: Float32Array; cosPh: Float32Array; dirt: Uint8Array }
export interface Field { W: number; H: number; data: Uint8ClampedArray; bounds: [number, number, number, number]; coarse?: Coarse }
/** What a view hands to the map to be drawn every animation frame: layers under and over the static ones, and whether anything is still moving. */
export interface Frame { under: Layer[]; over: Layer[]; settled: boolean }
export type Animated = (now: number) => Frame;

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

// The squares are blended into one soft surface: each pixel takes a distance-weighted average of nearby cells, so neighbouring values
// fade into each other and the basemap stays readable underneath.
const BLEND_RADIUS = 2.4; // in cells
const PX_PER_CELL = 3; // the GPU's linear filtering smooths the rest; fewer pixels keep hour changes instant
const ALPHA = 120, ALPHA_EXTREME = 175; // see-through enough for the streets underneath, solid enough to read the value
const cache = new WeakMap<AreasResp, Map<string, Field | null>>();

function makeCoarse(W: number, H: number, data: Uint8ClampedArray, dirt: Uint8Array): Coarse {
  const k = Math.max(1, Math.round(W / 96)), w = Math.ceil(W / k), h = Math.ceil(H / k), n = w * h;
  const c: Coarse = { w, h, mask: new Uint8Array(n), u: new Float32Array(n), sinPh: new Float32Array(n), cosPh: new Float32Array(n), dirt: new Uint8Array(n) };
  for (let cy = 0; cy < h; cy++) for (let cx = 0; cx < w; cx++) {
    const i = cy * w + cx, src = Math.min(H - 1, cy * k + (k >> 1)) * W + Math.min(W - 1, cx * k + (k >> 1));
    const fx = cx / w, fy = cy / h, ph = fx * 5.2 + fy * 3.4 + Math.sin(cx * 0.35) * 1.1; // long, slow waves rather than sparkle
    c.mask[i] = data[src * 4 + 3]; c.dirt[i] = dirt[src]; c.u[i] = fx * 0.75 + fy * 0.45; c.sinPh[i] = Math.sin(ph); c.cosPh[i] = Math.cos(ph);
  }
  return c;
}

/** Rasterise the area cells into a smooth RGBA field with thin contour lines where the value crosses a band edge. */
export function areaField(a: AreasResp, scale: "relative" | "absolute", rows: AreaRow[] = areaRows(a)): Field | null {
  const key = `${scale}:${a.metric}`;
  const hit = cache.get(a)?.get(key);
  if (hit !== undefined) return hit;
  const heat = a.metric === "heat";
  const { lo, hi } = relRange(rows.map((r) => r.mean));
  const range = Math.max(Math.abs(a.thresholds.cool ?? 0), Math.abs(a.thresholds.hot ?? 0), 1);
  const isExtreme = (r: AreaRow) => (heat ? (r.anomaly ?? 0) >= (a.thresholds.hot ?? 99) || (r.anomaly ?? 0) <= (a.thresholds.cool ?? -99)
    : r.mean >= (a.thresholds.clean ?? 101) || r.mean <= (a.thresholds.dirty ?? -1));
  const colorOf = (v: number): RGB => (heat ? heatColor(v, range) : scale === "relative" ? relColor(v, lo, hi) : caiColor(v));
  const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
  const dirtOf = (v: number) => (heat ? clamp01(v / range) : scale === "relative" ? clamp01(1 - (v - lo) / Math.max(1, hi - lo)) ** 1.4 : clamp01((88 - v) / 50));
  const valueOf = (r: AreaRow) => (heat ? r.anomaly ?? 0 : r.mean);
  let out: Field | null = null;
  if (rows.length) {
    const lat0 = rows.reduce((t, r) => t + r.lat, 0) / rows.length, lon0 = rows.reduce((t, r) => t + r.lon, 0) / rows.length;
    const kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110574;
    const cell = a.cell_m, R = BLEND_RADIUS * cell, step = cell / PX_PER_CELL;
    const xs = rows.map((r) => (r.lon - lon0) * kx), ys = rows.map((r) => (r.lat - lat0) * ky);
    const minX = Math.min(...xs) - R, maxX = Math.max(...xs) + R, minY = Math.min(...ys) - R, maxY = Math.max(...ys) + R;
    const W = Math.min(2400, Math.ceil((maxX - minX) / step)), H = Math.min(2400, Math.ceil((maxY - minY) / step));
    const sx = (maxX - minX) / W, sy = (maxY - minY) / H;
    const invR2 = 1 / (R * R);
    const bucket = new Map<number, number[]>();
    const bk = (bx: number, by: number) => bx * 100003 + by;
    xs.forEach((x, i) => { const k = bk(Math.floor((x - minX) / R), Math.floor((ys[i] - minY) / R)); (bucket.get(k) ?? bucket.set(k, []).get(k)!).push(i); });
    const vals = rows.map(valueOf), ext = rows.map((r) => (isExtreme(r) ? 1 : 0));
    const data = new Uint8ClampedArray(W * H * 4);
    const covered = new Uint8Array(W * H);
    const dirt = new Uint8Array(W * H);
    for (let py = 0; py < H; py++) {
      const y = maxY - (py + 0.5) * sy, by = Math.floor((y - minY) / R);
      for (let px = 0; px < W; px++) {
        const x = minX + (px + 0.5) * sx, bx = Math.floor((x - minX) / R);
        let sw = 0, sv = 0, se = 0;
        for (let ox = -1; ox <= 1; ox++) for (let oy = -1; oy <= 1; oy++) {
          const list = bucket.get(bk(bx + ox, by + oy));
          if (!list) continue;
          for (let q = 0; q < list.length; q++) {
            const i = list[q], dx = xs[i] - x, dy = ys[i] - y, d2 = (dx * dx + dy * dy) * invR2;
            if (d2 >= 1) continue;
            const d = Math.sqrt(d2), w = (1 - d) * (1 - d);
            sw += w; sv += w * vals[i]; se += w * ext[i];
          }
        }
        const idx = py * W + px;
        if (sw < 1e-6) continue;
        covered[idx] = 1;
        const v = sv / sw, c = colorOf(v), o = idx * 4;
        dirt[idx] = Math.round(dirtOf(v) * 255);
        const fade = Math.min(1, Math.max(0, (sw - 0.02) / 0.2));
        data[o] = c[0]; data[o + 1] = c[1]; data[o + 2] = c[2];
        data[o + 3] = Math.round(fade * fade * (3 - 2 * fade) * (ALPHA + (ALPHA_EXTREME - ALPHA) * (se / sw)));
      }
    }
    // pixels with no cell nearby borrow the colour (not the opacity) of a neighbour, so the smooth edge fades to clear instead of to black
    for (let py = 0; py < H; py++) for (let px = 0; px < W; px++) {
      const i = py * W + px;
      if (covered[i]) continue;
      const src = px > 0 && (covered[i - 1] || data[(i - 1) * 4 + 3] === 0 && (data[(i - 1) * 4] | data[(i - 1) * 4 + 1] | data[(i - 1) * 4 + 2])) ? i - 1 : py > 0 ? i - W : -1;
      if (src >= 0) { data[i * 4] = data[src * 4]; data[i * 4 + 1] = data[src * 4 + 1]; data[i * 4 + 2] = data[src * 4 + 2]; }
    }
    out = { W, H, data, coarse: makeCoarse(W, H, data, dirt), bounds: [lon0 + minX / kx, lat0 + minY / ky, lon0 + maxX / kx, lat0 + maxY / ky] };
  }
  (cache.get(a) ?? cache.set(a, new Map()).get(a)!).set(key, out);
  return out;
}

const toCanvas = (f: { W: number; H: number; data: Uint8ClampedArray }) => {
  const c = document.createElement("canvas");
  c.width = f.W; c.height = f.H;
  c.getContext("2d")!.putImageData(new ImageData(f.data as Uint8ClampedArray<ArrayBuffer>, f.W, f.H), 0, 0);
  return c;
};

const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
};

/**
 * Cross-fades between area fields (hour, scale or layer changes) so the surface morphs instead of jumping, and adds a little ambient life:
 * a wide soft sweep that drifts across the surface and a slow breathing of the dirtier or hotter places. The ambient layer is applied on top of
 * whatever is showing (settled or mid-morph), so starting or ending a morph never makes it jump. In the light theme the sweep darkens and
 * saturates (a light sweep would vanish on a light map); in the dark theme it lightens.
 */
export class AreaSurface {
  private from: Field | null = null;
  private to: Field | null = null;
  private shown: Field | null = null;
  private t0 = 0;
  private dur = 0;
  private settledCanvas: HTMLCanvasElement | null = null;
  private scratch: Uint8ClampedArray | null = null;
  private sheen: { key: Field; canvases: HTMLCanvasElement[]; image: ImageData; flip: number } | null = null;

  set(next: Field | null) {
    if (next === this.to) return;
    // the field on screen becomes the start of the morph; copy it because the scratch buffer is reused
    this.from = this.shown && this.shown.data === this.scratch ? { ...this.shown, data: this.shown.data.slice() } : this.shown;
    if (this.from && next && (this.from.W !== next.W || this.from.H !== next.H)) this.from = null;
    this.to = next;
    this.t0 = performance.now();
    this.dur = reduced() ? 0 : this.from && next ? 450 : 650;
    this.settledCanvas = null;
  }

  frame(now: number): { layers: Layer[]; settled: boolean } {
    const t = this.dur === 0 ? 1 : Math.min(1, Math.max(0, (now - this.t0) / this.dur));
    let base: Field | null;
    if (t >= 1) base = this.to;
    else {
      const e = t * t * (3 - 2 * t), a = this.from, b = this.to, ref = (b ?? a)!;
      if (!this.scratch || this.scratch.length !== ref.data.length) this.scratch = new Uint8ClampedArray(ref.data.length);
      const data = this.scratch;
      for (let i = 0; i < data.length; i += 4) {
        const aa = a ? a.data[i + 3] * (1 - e) : 0, ab = b ? b.data[i + 3] * e : 0, al = aa + ab;
        if (al < 1) { data[i + 3] = 0; continue; }
        // premultiplied blend so a fading pixel keeps its own colour
        for (let k = 0; k < 3; k++) data[i + k] = ((a ? a.data[i + k] * aa : 0) + (b ? b.data[i + k] * ab : 0)) / al;
        data[i + 3] = al;
      }
      base = { ...ref, data };
    }
    this.shown = base;
    if (!base) return { layers: [], settled: true };
    if (reduced()) {
      if (t >= 1) { this.settledCanvas ??= toCanvas(base); return { layers: [this.surfaceLayer(this.settledCanvas, base.bounds)], settled: true }; }
      return { layers: [this.surfaceLayer(toCanvas(base), base.bounds)], settled: false };
    }
    let surface: HTMLCanvasElement;
    if (t >= 1) { this.settledCanvas ??= toCanvas(base); surface = this.settledCanvas; } else surface = toCanvas(base);
    const amb = this.ambient(base, now);
    return { layers: [this.surfaceLayer(surface, base.bounds), ...(amb ? [this.surfaceLayer(amb.image, amb.bounds, "areas-ambient")] : [])], settled: false };
  }

  private surfaceLayer(image: HTMLCanvasElement, bounds: Field["bounds"], id = "areas-surface") {
    return new BitmapLayer({ id, image, bounds, pickable: false, textureParameters: { minFilter: "linear", magFilter: "linear" } } as never);
  }

  /** The ambient overlay is a tiny canvas stretched over the surface by the GPU, so each frame costs a few thousand pixels, not the whole raster. */
  private ambient(f: Field, now: number): { image: HTMLCanvasElement; bounds: Field["bounds"] } | null {
    const co = (this.to ?? f).coarse;
    if (!co) return null;
    if (!this.sheen || this.sheen.image.width !== co.w || this.sheen.image.height !== co.h) {
      const mk = () => { const c = document.createElement("canvas"); c.width = co.w; c.height = co.h; return c; };
      this.sheen = { key: f, canvases: [mk(), mk()], image: new ImageData(co.w, co.h), flip: 0 };
    }
    const sh = this.sheen, d = sh.image.data;
    // the shimmer stays out of the way of the forecast: it fades out while the data is changing (playing or scrubbing) and returns about a second after it settles
    const calm = Math.min(1, Math.max(0, (now - (this.t0 + this.dur) - 500) / 1000)), k = calm * calm * (3 - 2 * calm);
    const dark = isDark(), tt = now / 1000, pos = ((tt / 7) % 2) - 0.5, cw = Math.cos(tt * 0.95), sw = Math.sin(tt * 0.95);
    const [r, g, bl] = dark ? [226, 255, 234] : [10, 44, 28]; // the sweep lightens in the dark theme and deepens in the light one: the opposite would vanish
    for (let i = 0, n = co.w * co.h; i < n; i++) {
      const m = co.mask[i] / 255, o = i * 4;
      if (m < 0.04) { d[o + 3] = 0; continue; }
      const dd = co.u[i] - pos;
      let b = 1 - dd * dd * 16;
      b = b > 0 ? b * b : 0;
      const breathe = (co.dirt[i] / 255) * (0.5 + 0.5 * (co.sinPh[i] * cw + co.cosPh[i] * sw));
      d[o] = r; d[o + 1] = g; d[o + 2] = bl;
      d[o + 3] = k * m * (dark ? 85 * b + 60 * breathe : 120 * b + 75 * breathe);
    }
    sh.flip ^= 1;
    const cv = sh.canvases[sh.flip];
    cv.getContext("2d")!.putImageData(sh.image, 0, 0);
    return { image: cv, bounds: f.bounds };
  }
}

/** Near-invisible grid so hover and tooltips still work per cell. */
export function areaPickLayer(a: AreasResp, rows: AreaRow[]): Layer {
  return new GridCellLayer<AreaRow>({
    id: "areas", data: rows, getPosition: (r: AreaRow) => [r.lon, r.lat], cellSize: a.cell_m, coverage: 1, extruded: false, material: false,
    getFillColor: [0, 0, 0, 4], pickable: true, updateTriggers: { getFillColor: [a.metric, a.time] },
  } as never);
}

/** Radar-style pings and a soft glow on the worst places, drawn from the real "worst areas" list. */
export function pulseLayers(places: AreaPlace[], now: number, rgb: RGB = [196, 78, 58]): Layer[] {
  if (!places.length) return [];
  const motion = !reduced();
  type P = AreaPlace & { phase: number };
  const rings: P[] = places.flatMap((p, i) => [0, 0.5].map((o) => ({ ...p, phase: motion ? ((now / 2400) + i * 0.23 + o) % 1 : 0.4 + o * 0.3 })));
  return [
    new ScatterplotLayer<AreaPlace>({
      id: "area-glow", data: places, getPosition: (p: AreaPlace) => [p.lon, p.lat], radiusUnits: "pixels", getRadius: 30,
      getFillColor: [...rgb, 46] as never, stroked: false, pickable: false,
    } as never),
    new ScatterplotLayer<P>({
      id: "area-pulse", data: rings, getPosition: (p: P) => [p.lon, p.lat], radiusUnits: "pixels", stroked: true, filled: false, lineWidthUnits: "pixels",
      getRadius: (p: P) => 7 + 44 * p.phase, getLineWidth: 2.5,
      getLineColor: (p: P) => [...rgb, Math.round(230 * (1 - p.phase) ** 1.5)] as never,
      updateTriggers: { getRadius: now, getLineColor: now }, pickable: false,
    } as never),
    new ScatterplotLayer<AreaPlace>({
      id: "area-pulse-dot", data: places, getPosition: (p: AreaPlace) => [p.lon, p.lat], radiusUnits: "pixels", getRadius: 5,
      getFillColor: [...rgb, 255] as never, stroked: true, getLineColor: [255, 255, 255, 90] as never, lineWidthUnits: "pixels", getLineWidth: 3, pickable: false,
    } as never),
  ];
}

export const prefersReducedMotion = reduced;
