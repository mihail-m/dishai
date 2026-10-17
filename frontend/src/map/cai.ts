// Street geometry (binary, loaded once) and CAI colouring.
export interface Geometry {
  n: number;
  offsets: Uint32Array; // n+1
  coords: Float32Array; // 2P lon/lat
  classes: Uint8Array; // 0 major, 1 collector, 2 local, 3 path
}

export async function loadGeometry(): Promise<Geometry> {
  const buf = await (await fetch("/api/cai/geometry")).arrayBuffer();
  const dv = new DataView(buf);
  const n = dv.getUint32(0, true);
  const p = dv.getUint32(4, true);
  const offsets = new Uint32Array(buf.slice(8, 8 + 4 * (n + 1)));
  const co = 8 + 4 * (n + 1);
  const coords = new Float32Array(buf.slice(co, co + 8 * p));
  const classes = new Uint8Array(buf, co + 8 * p, n);
  return { n, offsets, coords, classes };
}

type RGB = [number, number, number];
type Stops = [number, RGB][];

function lut(stops: Stops, n: number): Uint8Array {
  const out = new Uint8Array((n + 1) * 3);
  for (let v = 0; v <= n; v++) {
    let k = 0;
    while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
    const [a, ca] = stops[k], [b, cb] = stops[k + 1];
    const t = Math.max(0, Math.min(1, (v - a) / (b - a)));
    for (let c = 0; c < 3; c++) out[v * 3 + c] = Math.round(ca[c] + (cb[c] - ca[c]) * t);
  }
  return out;
}

// Absolute CAI, 0 (dirty) to 100 (clean). Muted status ramp, always shown with a labelled legend.
export const ABS_STOPS: Stops = [
  [0, [150, 70, 78]], [20, [200, 100, 88]], [40, [220, 142, 84]], [60, [226, 188, 98]], [80, [152, 192, 112]], [100, [76, 160, 126]],
];
// Relative view: a calmer, desaturated ramp with no deep red, so small differences read as a gentle gradient rather than an alarm.
export const REL_STOPS: Stops = [
  [0, [206, 134, 114]], [25, [226, 176, 122]], [50, [230, 210, 148]], [75, [172, 202, 146]], [100, [116, 172, 144]],
];
export const LUT: Uint8Array = lut(ABS_STOPS, 100);
export const REL_LUT: Uint8Array = lut(REL_STOPS, 100);
export const caiColor = (v: number): [number, number, number] => {
  const i = Math.max(0, Math.min(100, Math.round(v))) * 3;
  return [LUT[i], LUT[i + 1], LUT[i + 2]];
};
export const caiCss = (v: number) => `rgb(${caiColor(v).join(",")})`;
export const relCss = (v: number) => { const i = Math.round(Math.max(0, Math.min(100, v))) * 3; return `rgb(${REL_LUT[i]},${REL_LUT[i + 1]},${REL_LUT[i + 2]})`; };

/** Relative scale: p5..p95 of the values stretched over the calm ramp. A minimum span keeps near-flat fields from being exaggerated. */
export const MIN_REL_SPAN = 10;
export function relRange(values: ArrayLike<number>): { lo: number; hi: number } {
  const s = Float32Array.from(values).sort();
  let lo = s[Math.floor(s.length * 0.05)], hi = s[Math.floor(s.length * 0.95)];
  if (hi - lo < MIN_REL_SPAN) { const m = (lo + hi) / 2; lo = m - MIN_REL_SPAN / 2; hi = m + MIN_REL_SPAN / 2; }
  return { lo, hi };
}
export const relColor = (v: number, lo: number, hi: number): [number, number, number] => {
  const i = Math.round(Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * 100) * 3;
  return [REL_LUT[i], REL_LUT[i + 1], REL_LUT[i + 2]];
};

const WIDTH_M = [14, 9, 6, 3]; // by road class, metres (scales with zoom)

/** Per-vertex colour/width arrays for a binary deck.gl PathLayer. relative=true uses the calm relative ramp over p5..p95. */
export function buildAttributes(g: Geometry, cai: ArrayLike<number>, relative: boolean, alpha = 235) {
  const P = g.coords.length / 2;
  const colors = new Uint8Array(P * 4);
  const widths = new Float32Array(P);
  const { lo, hi } = relative ? relRange(cai) : { lo: 0, hi: 100 };
  for (let s = 0; s < g.n; s++) {
    const [r, gg, b] = relative ? relColor(cai[s], lo, hi) : caiColor(cai[s]);
    const w = WIDTH_M[g.classes[s]];
    for (let k = g.offsets[s]; k < g.offsets[s + 1]; k++) {
      colors[k * 4] = r; colors[k * 4 + 1] = gg; colors[k * 4 + 2] = b; colors[k * 4 + 3] = alpha;
      widths[k] = w;
    }
  }
  return { colors, widths };
}
