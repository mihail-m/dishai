import { useMemo, useState } from "react";
import type { CalendarResp, PollenDay, PollenLevel, PollenSpecies, Species } from "../types";
import { SPECIES_IDS, SPECIES_LABEL } from "../prefs";

// Pollen levels are ordinal states, so they wear the app's status colours (never the only cue: every mark also has its value and level word).
const LEVELS: PollenLevel[] = ["none", "low", "moderate", "high"];
export const LEVEL_WORD: Record<PollenLevel, string> = { none: "none", low: "low", moderate: "moderate", high: "high" };
const TONE: Record<PollenLevel, string> = { none: "var(--ink-3)", low: "var(--good)", moderate: "var(--watch)", high: "var(--warn)" };
export const levelFill = (l: PollenLevel) => (l === "none" ? "var(--surface-2)" : `color-mix(in srgb, ${TONE[l]} ${l === "low" ? 55 : 85}%, var(--surface))`);
export const levelInk = (l: PollenLevel) => TONE[l];
const dayName = (date: string, today: string) => (date === today ? "Today" : new Date(`${date}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short" }));

/** One column per forecast day: height is the day's peak against 1.5 x the "high" band, so a species' columns compare with each other. */
export function DayBars({ days, high, today }: { days: PollenDay[]; high: number; today: string }) {
  const top = high * 1.5;
  return (
    <div className="grid grid-cols-4 gap-2" role="list" aria-label="Daily peak by day">
      {days.map((d) => {
        const h = d.peak == null ? 0 : Math.max(d.peak > 0 ? 0.06 : 0, Math.min(1, d.peak / top));
        const label = d.peak == null ? "no data" : `${d.peak} grains/m³, ${LEVEL_WORD[d.level]}${d.peak_hhmm ? `, peak ${d.peak_hhmm}` : ""}`;
        return (
          <div key={d.date} role="listitem" className="grid gap-1 text-center" title={`${dayName(d.date, today)}: ${label}`} aria-label={`${dayName(d.date, today)}: ${label}`}>
            <div className="relative h-14 rounded-md" style={{ background: "var(--surface-2)" }}>
              <div className="absolute inset-x-0 bottom-0 rounded-t-[4px]" style={{ height: `${h * 100}%`, background: d.peak == null ? "transparent" : levelFill(d.level), borderTop: h > 0 ? `3px solid ${levelInk(d.level)}` : undefined }} />
            </div>
            <div className="text-[11px] font-semibold tabular-nums">{d.peak == null ? "–" : d.peak}</div>
            <div className="text-[11px]" style={{ color: "var(--ink-3)" }}>{dayName(d.date, today)}</div>
          </div>
        );
      })}
    </div>
  );
}

/** Hourly line for the next 4 days with the moderate / high bands as reference lines and a hover or arrow-key readout. */
export function Sparkline({ s }: { s: PollenSpecies }) {
  const pts = s.hourly.filter((p) => p.grains != null) as { time: string; grains: number }[];
  const [hover, setHover] = useState<number | null>(null);
  const W = 320, H = 70, P = 4;
  const maxV = Math.max(s.bands.high * 1.2, ...pts.map((p) => p.grains));
  const x = (i: number) => P + (i / Math.max(1, pts.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / maxV) * (H - 2 * P);
  const path = useMemo(() => pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.grains).toFixed(1)}`).join(""), [s]); // eslint-disable-line react-hooks/exhaustive-deps
  if (pts.length < 2) return null;
  const cur = hover != null ? pts[hover] : null;
  const move = (clientX: number, el: SVGSVGElement) => {
    const r = el.getBoundingClientRect();
    setHover(Math.max(0, Math.min(pts.length - 1, Math.round(((clientX - r.left) / r.width * W - P) / (W - 2 * P) * (pts.length - 1)))));
  };
  const midnights = pts.map((p, i) => (p.time.slice(11, 13) === "00" && i > 0 ? i : -1)).filter((i) => i >= 0);
  return (
    <div>
      <div className="mb-1 h-4 text-[11px] tabular-nums" style={{ color: "var(--ink-2)" }} aria-live="polite">
        {cur ? `${new Date(cur.time).toLocaleDateString("en-GB", { weekday: "short" })} ${cur.time.slice(11, 16)} · ${cur.grains} grains/m³` : "Hourly, next 4 days"}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full touch-none" role="img" tabIndex={0} aria-label={`${s.label} pollen, hourly for the next four days. Peak ${Math.max(...pts.map((p) => p.grains))} grains per cubic metre.`}
        onPointerMove={(e) => move(e.clientX, e.currentTarget)} onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => { if (e.key === "ArrowRight") setHover((h) => Math.min(pts.length - 1, (h ?? -1) + 1)); else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? 1) - 1)); else if (e.key === "Escape") setHover(null); }}>
        {midnights.map((i) => <line key={i} x1={x(i)} x2={x(i)} y1={P} y2={H - P} stroke="var(--line)" strokeWidth={1} />)}
        {[s.bands.moderate, s.bands.high].filter((b) => b < maxV).map((b, k) => <line key={b} x1={P} x2={W - P} y1={y(b)} y2={y(b)} stroke={k ? "var(--warn)" : "var(--watch)"} strokeWidth={1} strokeDasharray="3 3" opacity={0.7} />)}
        <path d={`${path}L${x(pts.length - 1)},${H - P}L${x(0)},${H - P}Z`} fill="var(--accent)" opacity={0.12} />
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {cur && <><line x1={x(hover!)} x2={x(hover!)} y1={P} y2={H - P} stroke="var(--ink-3)" strokeWidth={1} /><circle cx={x(hover!)} cy={y(cur.grains)} r={4} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} /></>}
      </svg>
      <div className="mt-1 flex gap-3 text-[11px]" style={{ color: "var(--ink-3)" }}>
        <span><i className="mr-1 inline-block w-3 align-middle" style={{ borderTop: "1px dashed var(--watch)" }} />moderate from {s.bands.moderate}</span>
        <span><i className="mr-1 inline-block w-3 align-middle" style={{ borderTop: "1px dashed var(--warn)" }} />high from {s.bands.high}</span>
      </div>
    </div>
  );
}

const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const binName = (b: number) => `${MONTH_NAMES[Math.floor(b / 3)]} ${["1–10", "11–20", "21–end"][b % 3]}`;

/** Species x 36 ten-day bins from the CAMS model history; today's bin is outlined. A table of typical dates sits behind it. */
export function CalendarHeat({ cal, highlight }: { cal: CalendarResp; highlight: Species[] }) {
  const [sel, setSel] = useState<{ k: Species; b: number } | null>(null);
  if (!cal.available || !cal.species) return <p className="muted m-0 text-sm">The season calendar has not been baked yet (python -m backend.data_prep.pollen_calendar).</p>;
  const sp = cal.species;
  const picked = sel ? sp[sel.k] : null;
  return (
    <div className="grid gap-2">
      <div className="overflow-x-auto">
        <div className="grid min-w-[520px] gap-1" style={{ gridTemplateColumns: "64px repeat(36, minmax(0, 1fr))" }} role="group" aria-label="Typical pollen season by species and ten-day period">
          <span />
          {MONTHS.map((m, i) => <span key={i} className="text-center text-[11px]" style={{ gridColumn: "span 3", color: "var(--ink-3)" }}>{m}</span>)}
          {SPECIES_IDS.map((k) => (
            <div key={k} className="contents">
              <span className={`self-center text-[12px] ${highlight.includes(k) ? "font-bold" : "font-medium"}`}>{SPECIES_LABEL[k]}</span>
              {sp[k].levels.map((lv, b) => {
                const level = LEVELS[lv];
                const now = b === cal.today_bin;
                return (
                  <button key={b} type="button" className="h-6 rounded-[3px] p-0" aria-label={`${SPECIES_LABEL[k]}, ${binName(b)}: ${sp[k].bins[b]} grains/m³ typical peak, ${LEVEL_WORD[level]}`}
                    title={`${SPECIES_LABEL[k]} · ${binName(b)} · ${sp[k].bins[b]} grains/m³ (${LEVEL_WORD[level]})`}
                    onMouseEnter={() => setSel({ k, b })} onFocus={() => setSel({ k, b })} onClick={() => setSel({ k, b })}
                    style={{ background: levelFill(level), outline: now ? "2px solid var(--ink)" : undefined, outlineOffset: now ? 1 : undefined }} />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="min-h-5 text-xs tabular-nums" style={{ color: "var(--ink-2)" }} aria-live="polite">
        {sel && picked ? `${SPECIES_LABEL[sel.k]} · ${binName(sel.b)}: typically up to ${picked.bins[sel.b]} grains/m³ (${LEVEL_WORD[LEVELS[picked.levels[sel.b]]]})` : "Hover or tap a square. The outlined column is today."}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[11px]" style={{ color: "var(--ink-3)" }}>
        {LEVELS.map((l) => <span key={l} className="flex items-center gap-1"><i className="inline-block h-3 w-3 rounded-[3px]" style={{ background: levelFill(l) }} />{LEVEL_WORD[l]}</span>)}
        <span>· typical highest hourly value in each 10 days, {cal.years?.[0]}–{cal.years?.[cal.years.length - 1]}</span>
      </div>
      <details className="acc">
        <summary>Typical season dates (table)</summary>
        <div className="acc-body">
          <table className="w-full text-xs">
            <thead><tr className="faint text-left"><th className="py-1 font-normal">Pollen</th><th className="font-normal">Usually starts</th><th className="font-normal">Usually ends</th><th className="font-normal">Years used</th></tr></thead>
            <tbody>
              {(cal.season ?? []).map((s) => (
                <tr key={s.type} className="border-t" style={{ borderColor: "var(--line)" }}>
                  <td className="py-1.5 font-semibold">{SPECIES_LABEL[s.type]}</td>
                  <td>{s.status === "none" ? "no stable season" : s.start}</td><td>{s.status === "none" ? "–" : s.end}</td>
                  <td className="tabular-nums">{sp[s.type].years.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="faint m-0 text-[11px]">Season = days when the modelled daily peak reaches the CAMS season threshold (10 grains/m³ for trees and mugwort, 3 for grass and ragweed), 5th to 95th percentile of those days, median over the years.</p>
        </div>
      </details>
    </div>
  );
}
