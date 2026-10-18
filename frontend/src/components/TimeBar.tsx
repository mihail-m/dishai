import { useEffect, useRef, useState } from "react";
import type { Forecast } from "../types";
import { PM_WARNING, PM_WATCH, dayTime } from "../api";
import type { PreloadStatus } from "../forecast";
import { Icon } from "./ui";

export type TimeBarMode = "air" | "heat" | "compact";
interface Props {
  mode: TimeBarMode;
  fc: Forecast | null; hourIndex: number; setHourIndex: (i: number) => void; ready: boolean;
  progress: PreloadStatus | null; error: string | null;
}

type Metric = "pm" | "trap" | "heat";
const W = 600, H = 100;
const CFG = {
  pm: { t1: PM_WATCH, t2: PM_WARNING, l1: `WHO ${PM_WATCH}`, l2: `EU ${PM_WARNING}`, words: ["Good", "Fair", "Poor"], unit: " µg/m³" },
  trap: { t1: 35, t2: 60, l1: "Watch 35", l2: "Warning 60", words: ["Low", "Building", "Trapped"], unit: "" },
  heat: { t1: 27, t2: 32, l1: "Warm 27°", l2: "Hot 32°", words: ["Comfortable", "Warm", "Hot"], unit: " °C" },
} as const;

/** The forecast chart and the time control in one: PM2.5 or the air trapping score over the next hours, drag or tap to move through time. */
export default function TimeBar({ mode, fc, hourIndex, setHourIndex, ready, progress, error }: Props) {
  const n = fc?.hours.length ?? 0;
  const [playing, setPlaying] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [pick, setPick] = useState<Metric>("pm");
  const latest = useRef({ hourIndex, n, setHourIndex });
  latest.current = { hourIndex, n, setHourIndex };
  useEffect(() => {
    if (!playing || !ready) return;
    const t = setInterval(() => { const l = latest.current; l.setHourIndex((l.hourIndex + 1) % Math.max(l.n, 1)); }, 600);
    return () => clearInterval(t);
  }, [playing, ready]);

  const hours = fc?.hours ?? [];
  const hasPm = hours.some((h) => (h.pm25_model ?? h.pm25) != null);
  const hasModel = hours.some((h) => h.pm25_model != null);
  const m: Metric = mode === "heat" ? "heat" : pick === "pm" && hasPm ? "pm" : "trap";
  const c = CFG[m];
  const vals = hours.map((h) => (m === "pm" ? h.pm25_model ?? h.pm25 : m === "heat" ? h.apparent ?? h.t2m : h.score));
  const raw = m === "pm" && hasModel ? hours.map((h) => h.pm25) : null; // the regional model (CAMS) before the learned correction
  const all = [...vals, ...(raw ?? [])].filter((v): v is number => v != null);
  const lo = m === "heat" ? Math.floor((Math.min(...all, c.t1) - 3) / 5) * 5 : 0;
  const hi = m === "pm" ? Math.max(32, Math.ceil((Math.max(...all, 0) + 4) / 5) * 5) : m === "heat" ? Math.max(38, Math.ceil((Math.max(...all, 0) + 3) / 5) * 5) : 100;
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  const y = (v: number) => H - 6 - ((v - lo) / (hi - lo)) * (H - 14);
  const curve = (arr: (number | null)[]) => {
    const pts = arr.map((v, i) => (v == null ? null : ([x(i), y(v)] as const))).filter((p): p is readonly [number, number] => !!p);
    return pts.length > 1 ? pts.reduce((d, [px, py], i) => {
      if (i === 0) return `M${px},${py}`;
      const [qx, qy] = pts[i - 1], mx = (qx + px) / 2;
      return `${d} C${mx},${qy} ${mx},${py} ${px},${py}`;
    }, "") : "";
  };
  const path = curve(vals), rawPath = raw ? curve(raw) : "";
  const bandHi = hours.map((h, i) => (h.pm25_p90 != null ? `${x(i).toFixed(1)},${y(h.pm25_p90).toFixed(1)}` : null));
  const bandLo = hours.map((h, i) => (h.pm25_p10 != null ? `${x(i).toFixed(1)},${y(h.pm25_p10).toFixed(1)}` : null));
  const band = m === "pm" && hasModel && bandHi.every(Boolean) && bandLo.every(Boolean) ? `${bandHi.join(" ")} ${[...bandLo].reverse().join(" ")}` : null;
  const cur = vals[hourIndex];
  const nums = vals.filter((v): v is number => v != null);
  const peakI = nums.length ? vals.indexOf(Math.max(...nums)) : -1;
  const days = hours.map((h, i) => ({ i, t: h.time })).filter(({ t }, k) => k > 0 && t.slice(11, 13) === "00");
  const h = hours[hourIndex];
  const isNow = fc ? hourIndex === fc.now_index : true;
  const pct = (i: number) => (n > 1 ? (i / (n - 1)) * 100 : 0);
  const toneOf = (v: number) => (v <= c.t1 ? 0 : v <= c.t2 ? 1 : 2);
  const TONE = ["good", "watch", "warn"] as const;
  const hv = hover != null ? hours[hover] : null, hvVal = hover != null ? vals[hover] : null;
  const y2 = y(c.t2) / H, y1 = y(c.t1) / H;
  const fmt = (v: number) => (m === "pm" ? `${Math.round(v)} µg/m³` : m === "heat" ? `${Math.round(v)} °C` : `${Math.round(v)} of 100`);

  if (mode === "compact") {
    return (
      <div className="timebar compact" role="group" aria-label="Forecast time">
        <button className="tb-play" onClick={() => setPlaying((p) => !p)} disabled={!ready || n < 2} aria-label={playing ? "Pause forecast" : "Play forecast"}>{playing ? Icon.pause : Icon.play}</button>
        <div className="flex items-baseline justify-between gap-3">
          <label htmlFor="hour" className="display text-[15px] font-bold">{h ? (isNow ? `Now · ${dayTime(h.time)}` : dayTime(h.time)) : "…"}</label>
          <div className="flex items-center gap-3">
            {!ready && <span className="faint text-xs" role="status" aria-live="polite">Preparing hours{progress ? ` · ${progress.loaded}/${progress.total}` : "…"}</span>}
            {fc && !isNow && <button className="text-xs font-bold" style={{ color: "var(--accent)" }} onClick={() => setHourIndex(fc.now_index)} disabled={!ready}>Back to now</button>}
          </div>
        </div>
        <div>
          <input id="hour" className="tb-slider" type="range" min={0} max={Math.max(n - 1, 0)} value={hourIndex} disabled={!ready} aria-label="Forecast hour" aria-valuetext={h ? dayTime(h.time) : undefined} onChange={(e) => setHourIndex(Number(e.target.value))} />
          <div className="tb-days" aria-hidden="true">
            {n > 1 && days.map(({ i, t }) => <span key={t} className="absolute whitespace-nowrap" style={{ left: `${pct(i)}%` }}>{new Date(t).toLocaleDateString("en-GB", { weekday: "short" })}</span>)}
          </div>
        </div>
        {(error || progress?.error) && (
          <p role="alert" className="m-0 text-xs" style={{ gridColumn: "1 / -1", color: "var(--warn)" }}>
            {error ? <>Could not update the forecast: {error}. {fc ? "The last loaded forecast is kept." : ""}</> : <>Could not prepare all {n} hours for this view: {progress?.error}.</>} Use Refresh forecast in the Live menu to retry.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="timebar" role="group" aria-label="Forecast time">
      <button className="tb-play" onClick={() => setPlaying((p) => !p)} disabled={!ready || n < 2} aria-label={playing ? "Pause forecast" : "Play forecast"}>{playing ? Icon.pause : Icon.play}</button>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <label htmlFor="hour" className="display flex flex-wrap items-baseline gap-x-3 text-[15px] font-bold">
          <span>{h ? (isNow ? `Now · ${dayTime(h.time)}` : dayTime(h.time)) : "…"}</span>
          {cur != null && <span className="text-[13px] tabular-nums" style={{ color: `var(--${TONE[toneOf(cur)]})` }}>{fmt(cur)} · {c.words[toneOf(cur)]}</span>}
        </label>
        <div className="flex items-center gap-2">
          {!ready && <span className="faint text-xs" role="status" aria-live="polite">Preparing hours{progress ? ` · ${progress.loaded}/${progress.total}` : "…"}</span>}
          {fc && !isNow && <button className="text-xs font-bold" style={{ color: "var(--accent)" }} onClick={() => setHourIndex(fc.now_index)} disabled={!ready}>Back to now</button>}
          {m === "heat" && <span className="tb-tabs"><button aria-pressed="true" tabIndex={-1}>Feels like</button></span>}
          {m !== "heat" && <div className="tb-tabs" role="group" aria-label="Forecast chart">
            <button aria-pressed={m === "pm"} disabled={!hasPm} onClick={() => setPick("pm")}>PM2.5</button>
            <button aria-pressed={m === "trap"} onClick={() => setPick("trap")} title="0 to 100: how strongly the air is trapped near the ground">Air trapping</button>
          </div>}
        </div>
      </div>
      <div>
        <div className="tb-track" onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width) * (n - 1))))); }} onPointerLeave={() => setHover(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <linearGradient id="tb-level" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={H}>
                <stop offset="0" style={{ stopColor: "var(--warn)" }} /><stop offset={Math.max(0, y2 - 0.04)} style={{ stopColor: "var(--warn)" }} />
                <stop offset={(y2 + y1) / 2} style={{ stopColor: "var(--watch)" }} />
                <stop offset={Math.min(1, y1 + 0.04)} style={{ stopColor: "var(--good)" }} /><stop offset="1" style={{ stopColor: "var(--good)" }} />
              </linearGradient>
              <linearGradient id="tb-fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" stopOpacity=".55" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></linearGradient>
              <mask id="tb-mask"><rect width={W} height={H} fill="url(#tb-fade)" /></mask>
            </defs>
            <line x1="0" x2={W} y1={y(c.t2)} y2={y(c.t2)} stroke="var(--warn)" strokeDasharray="3 5" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity=".45" />
            <line x1="0" x2={W} y1={y(c.t1)} y2={y(c.t1)} stroke="var(--good)" strokeDasharray="3 5" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity=".55" />
            {band && <polygon points={band} fill="var(--accent)" opacity=".13" />}
            {path && <path d={`${path} L${W},${H} L0,${H} Z`} fill="url(#tb-level)" mask="url(#tb-mask)" />}
            {rawPath && <path d={rawPath} fill="none" stroke="var(--ink-3)" strokeWidth="1.6" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" opacity=".8" />}
            {path && <path d={path} fill="none" stroke="url(#tb-level)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
            {days.map(({ i }) => <line key={i} x1={x(i)} x2={x(i)} y1="0" y2={H} stroke="var(--line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
            {fc && <line x1={x(fc.now_index)} x2={x(fc.now_index)} y1="0" y2={H} stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />}
            <line x1={x(hourIndex)} x2={x(hourIndex)} y1="0" y2={H} stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            {hover != null && <line x1={x(hover)} x2={x(hover)} y1="0" y2={H} stroke="var(--ink-3)" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity=".7" />}
          </svg>
          <span className="tb-lbl tb-hide-m" style={{ top: `${y1 * 100}%`, color: "var(--good)" }}>{c.l1}</span>
          <span className="tb-lbl tb-hide-m" style={{ top: `${y2 * 100}%`, color: "var(--warn)" }}>{c.l2}</span>
          {peakI >= 0 && peakI !== hourIndex && <span className="tb-peak tb-hide-m" style={{ left: `${pct(peakI)}%`, top: `${(y(vals[peakI]!) / H) * 100}%` }}>Peak {Math.round(vals[peakI]!)}{m === "heat" ? "°" : ""}</span>}
          {n > 1 && cur != null && <i className="tb-thumb" style={{ left: `${pct(hourIndex)}%`, top: `${(y(cur) / H) * 100}%` }} />}
          {hv && hvVal != null && hover !== hourIndex && (
            <span className="tb-tip" style={{ left: `${Math.max(14, Math.min(86, pct(hover!)))}%` }}>
              {dayTime(hv.time)} · <b className="tabular-nums">{fmt(hvVal)}</b>
              {m === "pm" && hv.pm25_p10 != null && hv.pm25_p90 != null && <span className="tb-tip-sub"> (likely {Math.round(hv.pm25_p10)} to {Math.round(hv.pm25_p90)})</span>}
              {raw && hv.pm25 != null && <span className="tb-tip-sub"> · regional model {Math.round(hv.pm25)}</span>}
            </span>
          )}
          <input id="hour" type="range" min={0} max={Math.max(n - 1, 0)} value={hourIndex} disabled={!ready} aria-label="Forecast hour" aria-valuetext={h ? dayTime(h.time) : undefined} onChange={(e) => setHourIndex(Number(e.target.value))} />
        </div>
        <div className="tb-days" aria-hidden="true">
          {n > 1 && days.map(({ i, t }) => (
            <span key={t} className="absolute whitespace-nowrap pl-1" style={{ left: `${pct(i)}%` }}>{new Date(t).toLocaleDateString("en-GB", { weekday: "short" })}</span>
          ))}
        </div>
        <p className="faint tb-hide-m m-0 mt-0.5 text-[11px] leading-snug">
          {m === "heat" ? "Feels-like temperature forecast for the city. Warm from 27°, hot from 32°. The map adds how much warmer or cooler each place usually is at that hour."
            : m === "pm"
            ? `Solid line: learned estimate for the whole city${band ? ", shaded: 10th to 90th percentile" : ""}.${raw ? " Dashed: regional model (CAMS, about 11 km), which runs high in episodes." : ""}`
            : "Air trapping score, 0 to 100: higher means more pollution stays near the ground. 35 and above is watch, 60 and above is an inversion warning."}
        </p>
      </div>
      {(error || progress?.error) && (
        <p role="alert" className="m-0 text-xs" style={{ gridColumn: "1 / -1", color: "var(--warn)" }}>
          {error ? <>Could not update the forecast: {error}. {fc ? "The last loaded forecast is kept." : ""}</> : <>Could not prepare all {n} hours for this view: {progress?.error}.</>} Use Refresh forecast in the Live menu to retry.
        </p>
      )}
    </div>
  );
}
