import { BitmapLayer, ScatterplotLayer, SolidPolygonLayer } from "@deck.gl/layers";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { AreaPlace, AreasResp } from "../types";
import { caiColor, relColor, relRange } from "./cai";
import { areaRows, heatColor, type AreaRow } from "./layers";

type RGB = [number, number, number];
export interface Field { W: number; H: number; data: Uint8ClampedArray; bounds: [number, number, number, number] }
/** What a view hands to the map to be drawn every animation frame: layers under and over the static ones, and whether anything is still moving. */
export interface Frame { under: Layer[]; over: Layer[]; settled: boolean }
export type Animated = (now: number) => Frame;

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

// The squares are blended into one soft surface: each pixel takes a distance-weighted average of nearby cells, so neighbouring values
// fade into each other and the basemap stays readable underneath.
const BLEND_RADIUS = 3.4; // in cells: wide and flat-topped, so a cell does not show as its own little bump
const PX_PER_CELL = 3; // the GPU's linear filtering smooths the rest; fewer pixels keep hour changes instant
const ALPHA = 120, ALPHA_EXTREME = 175; // see-through enough for the streets underneath, solid enough to read the value
const cache = new WeakMap<AreasResp, Map<string, Field | null>>();

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
  // the colour ramps are lookup tables with one entry per score point; blend between entries so neighbouring pixels do not flip between two steps (that read as a pixelated patchwork)
  const between = (at: (i: number) => RGB, u: number): RGB => {
    const x = Math.max(0, Math.min(100, u)), i = Math.min(99, Math.floor(x)), t = x - i, a = at(i), b = at(i + 1);
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  };
  const colorOf = (v: number): RGB => (heat ? heatColor(v, range)
    : scale === "relative" ? between((i) => relColor(lo + ((hi - lo) * i) / 100, lo, hi), ((v - lo) / Math.max(1e-6, hi - lo)) * 100)
    : between((i) => caiColor(i), v));
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
            const kq = 1 - d2, w = kq * kq; // smooth biweight kernel
            sw += w; sv += w * vals[i]; se += w * ext[i];
          }
        }
        const idx = py * W + px;
        if (sw < 1e-6) continue;
        covered[idx] = 1;
        const v = sv / sw, c = colorOf(v), o = idx * 4;
        const fade = Math.min(1, Math.max(0, (sw - 0.04) / 0.45));
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
    out = { W, H, data, bounds: [lon0 + minX / kx, lat0 + minY / ky, lon0 + maxX / kx, lat0 + maxY / ky] };
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
    this.dur = reduced() || (!this.from && !next) ? 0 : this.from && next ? 450 : 650; // nothing on screen and nothing to show: nothing to animate
    this.settledCanvas = null;
  }

  frame(now: number): { layers: Layer[]; settled: boolean } {
    const t = this.dur === 0 ? 1 : Math.min(1, Math.max(0, (now - this.t0) / this.dur));
    let base: Field | null;
    if (t >= 1) base = this.to;
    else {
      const e = t * t * (3 - 2 * t), a = this.from, b = this.to, ref = b ?? a;
      if (!ref) { this.shown = null; return { layers: [], settled: true }; }
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
    const k = this.calm(now);
    return { layers: [this.surfaceLayer(this.flow(base, now, k), base.bounds)], settled: false };
  }

  private surfaceLayer(image: HTMLCanvasElement, bounds: Field["bounds"], id = "areas-surface", opacity = 1) {
    return new BitmapLayer({ id, image, bounds, opacity, pickable: false, textureParameters: { minFilter: "linear", magFilter: "linear" } } as never);
  }

  /** How far the shimmer has faded in: it is off while the data is changing (playing or scrubbing) and returns about a second after it settles. */
  private calm(now: number) {
    const c = Math.min(1, Math.max(0, (now - (this.t0 + this.dur) - 500) / 1000));
    return c * c * (3 - 2 * c);
  }

  /**
   * The flowing colour field: every frame the field is read through a slow, smooth swirl (a warped sampling position), so the colours drift like
   * currents, and a soft travelling light-and-dark variation rides along so the flow also shows over evenly coloured areas. The result is drawn at half
   * resolution and smoothed by the GPU. The swirl is the only thing that moves: values are never changed, and it stands still (k = 0) while the
   * forecast is playing or being scrubbed.
   */
  private flow(f: Field, now: number, k: number): HTMLCanvasElement {
    const w = Math.max(2, f.W >> 1), h = Math.max(2, f.H >> 1);
    if (!this.sheen || this.sheen.image.width !== w || this.sheen.image.height !== h) {
      const mk = () => { const c = document.createElement("canvas"); c.width = w; c.height = h; return c; };
      this.sheen = { key: f, canvases: [mk(), mk()], image: new ImageData(w, h), flip: 0 };
    }
    const sh = this.sheen, d = sh.image.data, src = f.data, W = f.W, H = f.H, t = now / 1000;
    const A = 0.035 * k, N = 0.16 * k;
    // separable pieces of the swirl and the light variation: trig is evaluated per column and per row, not per pixel
    const cx = (fn: (u: number) => number) => { const o = new Float32Array(w); for (let x = 0; x < w; x++) o[x] = fn(x / w); return o; };
    const cy = (fn: (v: number) => number) => { const o = new Float32Array(h); for (let y = 0; y < h; y++) o[y] = fn(y / h); return o; };
    const rowDx1 = cy((v) => Math.sin(v * 3.1 + t * 0.35)), colDy1 = cx((u) => Math.sin(u * 2.7 - t * 0.31));
    const uS = cx((u) => Math.sin(2.3 * u - t * 0.27)), uC = cx((u) => Math.cos(2.3 * u - t * 0.27)), vS = cy((v) => Math.sin(2.3 * v)), vC = cy((v) => Math.cos(2.3 * v));
    const u2S = cx((u) => Math.sin(2.1 * u + t * 0.23)), u2C = cx((u) => Math.cos(2.1 * u + t * 0.23)), v2S = cy((v) => Math.sin(2.1 * v)), v2C = cy((v) => Math.cos(2.1 * v));
    const nxS = cx((u) => Math.sin(9 * u - t * 0.9)), nxC = cx((u) => Math.cos(9 * u - t * 0.9)), nyS = cy((v) => Math.sin(5 * v)), nyC = cy((v) => Math.cos(5 * v));
    const mxS = cx((u) => Math.sin(-3 * u)), mxC = cx((u) => Math.cos(-3 * u)), myS = cy((v) => Math.sin(7 * v + t * 0.6)), myC = cy((v) => Math.cos(7 * v + t * 0.6));
    for (let y = 0; y < h; y++) {
      const fy = y / h;
      for (let x = 0; x < w; x++) {
        const fx = x / w;
        const dx = A * (0.6 * rowDx1[y] + 0.4 * (uS[x] * vC[y] + uC[x] * vS[y]));
        const dy = A * (0.6 * colDy1[x] + 0.4 * (u2C[x] * v2C[y] + u2S[x] * v2S[y]));
        // bilinear read at the swirled position, so the half-resolution result stays smooth
        let sx = (fx + dx) * (W - 1), sy = (fy + dy) * (H - 1);
        sx = sx < 0 ? 0 : sx > W - 1 ? W - 1 : sx; sy = sy < 0 ? 0 : sy > H - 1 ? H - 1 : sy;
        const x0 = sx | 0, y0 = sy | 0, x1 = x0 + 1 < W ? x0 + 1 : x0, y1 = y0 + 1 < H ? y0 + 1 : y0, tx = sx - x0, ty = sy - y0;
        const i00 = (y0 * W + x0) * 4, i10 = (y0 * W + x1) * 4, i01 = (y1 * W + x0) * 4, i11 = (y1 * W + x1) * 4;
        const w00 = (1 - tx) * (1 - ty), w10 = tx * (1 - ty), w01 = (1 - tx) * ty, w11 = tx * ty, o = (y * w + x) * 4;
        const n = N * 0.5 * (nxS[x] * nyC[y] + nxC[x] * nyS[y] + myS[y] * mxC[x] + myC[y] * mxS[x]), m = 1 + n;
        const r = (src[i00] * w00 + src[i10] * w10 + src[i01] * w01 + src[i11] * w11) * m;
        const g = (src[i00 + 1] * w00 + src[i10 + 1] * w10 + src[i01 + 1] * w01 + src[i11 + 1] * w11) * m;
        const b = (src[i00 + 2] * w00 + src[i10 + 2] * w10 + src[i01 + 2] * w01 + src[i11 + 2] * w11) * m;
        const a = (src[i00 + 3] * w00 + src[i10 + 3] * w10 + src[i01 + 3] * w01 + src[i11 + 3] * w11) * (1 + 0.8 * n);
        d[o] = r > 255 ? 255 : r; d[o + 1] = g > 255 ? 255 : g; d[o + 2] = b > 255 ? 255 : b; d[o + 3] = a > 255 ? 255 : a < 0 ? 0 : a;
      }
    }
    sh.flip ^= 1;
    const cv = sh.canvases[sh.flip];
    cv.getContext("2d")!.putImageData(sh.image, 0, 0);
    return cv;
  }
}

/** The area cell under the pointer (within three quarters of a cell), found by distance: no layer is drawn for hover, so nothing can show through the surface. */
export function nearestRow(rows: AreaRow[], lon: number, lat: number, cellM: number): AreaRow | null {
  const kx = 111320 * Math.cos((lat * Math.PI) / 180), ky = 110574, limit = (cellM * 0.75) ** 2;
  let best: AreaRow | null = null, bd = limit;
  for (const r of rows) {
    const dx = (r.lon - lon) * kx, dy = (r.lat - lat) * ky, d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = r; }
  }
  return best;
}

/** A small, slow ping on the worst places (from the real "worst areas" list). */
export function pulseLayers(places: AreaPlace[], now: number, rgb: RGB = [196, 78, 58]): Layer[] {
  if (!places.length) return [];
  const phase = reduced() ? 0.4 : (now / 3400) % 1;
  return [
    new ScatterplotLayer<AreaPlace>({
      id: "area-pulse", data: places, getPosition: (p: AreaPlace) => [p.lon, p.lat], radiusUnits: "pixels", stroked: true, filled: false, lineWidthUnits: "pixels",
      getRadius: 5 + 15 * phase, getLineWidth: 1.5, getLineColor: [...rgb, Math.round(150 * (1 - phase) ** 1.6)] as never,
      updateTriggers: { getRadius: now, getLineColor: now }, pickable: false,
    } as never),
    new ScatterplotLayer<AreaPlace>({
      id: "area-pulse-dot", data: places, getPosition: (p: AreaPlace) => [p.lon, p.lat], radiusUnits: "pixels", getRadius: 3.2,
      getFillColor: [...rgb, 230] as never, stroked: false, pickable: false,
    } as never),
  ];
}

export const prefersReducedMotion = reduced;

/**
 * Hover and tooltips for the area surface. One faint rectangle over the whole city (a per-cell grid tinted only the cells that have streets, which showed
 * as a patchwork of squares on the smooth surface); the cell under the pointer is found by distance and handed on as the picked object.
 */
class AreaPickLayer extends SolidPolygonLayer<{ ring: [number, number][] }> {
  static layerName = "AreaPickLayer";
  getPickingInfo({ info }: { info: PickingInfo }) {
    const { rows, cellM } = this.props as unknown as { rows: AreaRow[]; cellM: number };
    const hit = info.picked && info.coordinate ? nearestRow(rows, info.coordinate[0], info.coordinate[1], cellM) : null;
    return { ...info, object: hit ?? undefined, picked: !!hit };
  }
}

export function areaPickLayer(a: AreasResp, rows: AreaRow[]): Layer {
  const lat = rows.reduce((t, r) => t + r.lat, 0) / Math.max(1, rows.length);
  const padX = (0.75 * a.cell_m) / (111320 * Math.cos((lat * Math.PI) / 180)), padY = (0.75 * a.cell_m) / 110574; // as far out as nearestRow reaches
  const x0 = Math.min(...rows.map((r) => r.lon)) - padX, x1 = Math.max(...rows.map((r) => r.lon)) + padX;
  const y0 = Math.min(...rows.map((r) => r.lat)) - padY, y1 = Math.max(...rows.map((r) => r.lat)) + padY;
  return new AreaPickLayer({
    id: "areas", data: [{ ring: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]] }], getPolygon: (d: { ring: [number, number][] }) => d.ring,
    getFillColor: [0, 0, 0, 1], pickable: true, rows, cellM: a.cell_m,
  } as never);
}
