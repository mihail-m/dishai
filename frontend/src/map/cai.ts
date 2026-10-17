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

// CAI 100 (clean) to 0 (dirty). Status-style ramp, always shown with a labelled legend.
const STOPS: [number, [number, number, number]][] = [
  [0, [122, 26, 40]], [15, [214, 69, 61]], [35, [232, 116, 44]], [55, [224, 176, 48]], [75, [127, 191, 90]], [100, [42, 157, 111]],
];
export const LUT: Uint8Array = (() => {
  const out = new Uint8Array(101 * 3);
  for (let v = 0; v <= 100; v++) {
    let k = 0;
    while (k < STOPS.length - 2 && v > STOPS[k + 1][0]) k++;
    const [a, ca] = STOPS[k], [b, cb] = STOPS[k + 1];
    const t = Math.max(0, Math.min(1, (v - a) / (b - a)));
    for (let c = 0; c < 3; c++) out[v * 3 + c] = Math.round(ca[c] + (cb[c] - ca[c]) * t);
  }
  return out;
})();
export const caiColor = (v: number): [number, number, number] => {
  const i = Math.max(0, Math.min(100, Math.round(v))) * 3;
  return [LUT[i], LUT[i + 1], LUT[i + 2]];
};
export const caiCss = (v: number) => `rgb(${caiColor(v).join(",")})`;

const WIDTH_M = [14, 9, 6, 3]; // by road class, metres (scales with zoom)

export function stretch(cai: ArrayLike<number>): { lo: number; hi: number } {
  const s = Float32Array.from(cai).sort();
  return { lo: s[Math.floor(s.length * 0.05)], hi: s[Math.floor(s.length * 0.95)] };
}

/** Per-vertex colour/width arrays for a binary deck.gl PathLayer. relative=true stretches p5..p95 to 0..100. */
export function buildAttributes(g: Geometry, cai: ArrayLike<number>, relative: boolean, alpha = 235) {
  const P = g.coords.length / 2;
  const colors = new Uint8Array(P * 4);
  const widths = new Float32Array(P);
  const { lo, hi } = relative ? stretch(cai) : { lo: 0, hi: 100 };
  const span = Math.max(1, hi - lo);
  for (let s = 0; s < g.n; s++) {
    const v = relative ? ((cai[s] - lo) / span) * 100 : cai[s];
    const i = Math.max(0, Math.min(100, Math.round(v))) * 3;
    const r = LUT[i], gg = LUT[i + 1], b = LUT[i + 2];
    const w = WIDTH_M[g.classes[s]];
    for (let k = g.offsets[s]; k < g.offsets[s + 1]; k++) {
      colors[k * 4] = r; colors[k * 4 + 1] = gg; colors[k * 4 + 2] = b; colors[k * 4 + 3] = alpha;
      widths[k] = w;
    }
  }
  return { colors, widths };
}
