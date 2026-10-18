import { BitmapLayer } from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";
import type { NetworkResp } from "../types";

const SUPER = 2; // the grain is drawn at this many times the resolution of the blurred raster
const PX_M = 70; // metres per pixel of the haze raster: coarse on purpose, it should read as an area, not as streets
const BLUR_M = 560; // how far a clean path spreads into its surroundings
const GRAIN = 0.6; // 0 = smooth, 1 = every pixel randomly between nothing and full strength
const INK: [number, number, number] = [9, 66, 42]; // deep green
const cache = new WeakMap<NetworkResp, { canvas: HTMLCanvasElement; bounds: [number, number, number, number] } | null>();

/** Three box blurs approximate a Gaussian; each pass runs along rows then columns of a Float32 raster. */
function blur(a: Float32Array, W: number, H: number, r: number) {
  const tmp = new Float32Array(a.length);
  const pass = (src: Float32Array, dst: Float32Array, n: number, stride: number, lines: number, lineStride: number) => {
    const win = 2 * r + 1;
    for (let l = 0; l < lines; l++) {
      const o = l * lineStride;
      let sum = 0;
      for (let i = -r; i <= r; i++) sum += src[o + Math.min(n - 1, Math.max(0, i)) * stride];
      for (let i = 0; i < n; i++) {
        dst[o + i * stride] = sum / win;
        sum += src[o + Math.min(n - 1, i + r + 1) * stride] - src[o + Math.max(0, i - r) * stride];
      }
    }
  };
  for (let k = 0; k < 3; k++) { pass(a, tmp, W, 1, H, W); pass(tmp, a, H, W, W, 1); }
}

/** Deterministic noise in 0..1, so the grain does not shimmer between renders. */
const hash = (x: number, y: number) => { let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263); h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967295; };

/**
 * The clean paths as a soft, granular deep-green haze: every clean path segment adds weight (more for the streets the cleanest routes use most)
 * to a coarse raster, which is blurred into a cloud over the districts those paths serve. It follows the area, not the street lines.
 */
export function hazeLayer(n: NetworkResp): Layer | null {
  let built = cache.get(n);
  if (built === undefined) {
    const segs = n.segments.filter((s) => s.channel && s.path.length > 1);
    if (!segs.length) built = null;
    else {
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const s of segs) for (const [x, y] of s.path) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
      const lat0 = (y0 + y1) / 2, kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110574;
      const pad = 1500;
      const minX = x0 * kx - pad, minY = y0 * ky - pad;
      const W = Math.min(900, Math.ceil((x1 * kx - minX + pad) / PX_M)), H = Math.min(900, Math.ceil((y1 * ky - minY + pad) / PX_M));
      const sx = W / (x1 * kx - minX + pad), sy = H / (y1 * ky - minY + pad);
      const d = new Float32Array(W * H);
      const maxUse = Math.max(1, ...segs.map((s) => s.usage_clean));
      for (const s of segs) {
        const w = 0.35 + Math.sqrt(s.usage_clean / maxUse);
        for (let i = 1; i < s.path.length; i++) {
          const ax = (s.path[i - 1][0] * kx - minX) * sx, ay = (s.path[i - 1][1] * ky - minY) * sy, bx = (s.path[i][0] * kx - minX) * sx, by = (s.path[i][1] * ky - minY) * sy;
          const steps = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) * 2));
          for (let k = 0; k <= steps; k++) {
            const px = Math.round(ax + ((bx - ax) * k) / steps), py = Math.round(ay + ((by - ay) * k) / steps);
            if (px >= 0 && px < W && py >= 0 && py < H) d[py * W + px] += w / steps * 2;
          }
        }
      }
      blur(d, W, H, Math.max(1, Math.round(BLUR_M / PX_M / 2)));
      const sorted = Array.from(d).filter((v) => v > 1e-6).sort((a, b) => a - b);
      const ref = sorted[Math.floor(sorted.length * 0.97)] || 1;
      const canvas = document.createElement("canvas");
      const CW = W * SUPER, CH = H * SUPER;
      canvas.width = CW; canvas.height = CH;
      const ctx = canvas.getContext("2d");
      if (!ctx) built = null;
      else {
        const img = ctx.createImageData(CW, CH);
        const at = (x: number, y: number) => d[Math.min(H - 1, Math.max(0, y)) * W + Math.min(W - 1, Math.max(0, x))];
        for (let cy = 0; cy < CH; cy++) {
          const fy = (cy + 0.5) / SUPER - 0.5, y0 = Math.floor(fy), ty = fy - y0;
          for (let cx = 0; cx < CW; cx++) {
            const fx = (cx + 0.5) / SUPER - 0.5, x0 = Math.floor(fx), tx = fx - x0;
            const dv = (at(x0, y0) * (1 - tx) + at(x0 + 1, y0) * tx) * (1 - ty) + (at(x0, y0 + 1) * (1 - tx) + at(x0 + 1, y0 + 1) * tx) * ty; // bilinear
            const v = Math.min(1, dv / ref), t = v * v * (3 - 2 * v); // smoothstep: soft edge, solid core
            const o = ((CH - 1 - cy) * CW + cx) * 4; // raster rows run south to north, the canvas top to bottom
            img.data[o] = INK[0]; img.data[o + 1] = INK[1]; img.data[o + 2] = INK[2];
            img.data[o + 3] = 175 * t * (1 - GRAIN + GRAIN * hash(cx, cy));
          }
        }
        ctx.putImageData(img, 0, 0);
        built = { canvas, bounds: [(minX) / kx, minY / ky, (minX + W / sx) / kx, (minY + H / sy) / ky] };
      }
    }
    cache.set(n, built);
  }
  if (!built) return null;
  // nearest-neighbour keeps the grain crisp when the raster is scaled up to the screen
  return new BitmapLayer({ id: "clean-haze", image: built.canvas, bounds: built.bounds, pickable: false, textureParameters: { minFilter: "linear", magFilter: "nearest" } } as never);
}
