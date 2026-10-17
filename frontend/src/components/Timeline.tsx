import {
  Area, AreaChart, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Forecast } from "../types";
import { LEVEL_TEXT, PM_WARNING, PM_WATCH, dayHour, hhmm, levelOf } from "../api";

interface Props { fc: Forecast; hourIndex: number; onPick: (i: number) => void }

const axis = { fontSize: 11, fill: "var(--ink-3)" };
const grid = { stroke: "var(--line)", strokeDasharray: "2 4" };

type Row = { time: string; score: number; pm25: number | null; model: number | null; band: [number, number] | null };

function Tip({ active, payload, unit, kind }: { active?: boolean; payload?: { payload: Row }[]; unit: string; kind: "score" | "pm" }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="card px-3 py-2 text-xs shadow-md">
      <div className="faint">{dayHour(p.time)}</div>
      {kind === "score" ? (
        <div className="font-semibold">Score {Math.round(p.score)} · {LEVEL_TEXT[levelOf(p.score)]}</div>
      ) : (
        <>
          <div className="font-semibold">{(p.model ?? p.pm25) == null ? "n/a" : `${(p.model ?? p.pm25)!.toFixed(1)} ${unit}`}</div>
          {p.band != null && <div className="faint">80 % range {p.band[0].toFixed(0)}–{p.band[1].toFixed(0)}</div>}
          {p.model != null && p.pm25 != null && <div className="faint">regional model (CAMS) {p.pm25.toFixed(1)}</div>}
        </>
      )}
    </div>
  );
}

export default function Timeline({ fc, hourIndex, onPick }: Props) {
  const data: Row[] = fc.hours.map((h) => ({
    time: h.time, score: h.score, pm25: h.pm25, model: h.pm25_model ?? null,
    band: h.pm25_p10 != null && h.pm25_p90 != null ? [h.pm25_p10, h.pm25_p90] : null,
  }));
  const sel = data[hourIndex]?.time;
  const now = data[fc.now_index]?.time;
  const pick = (s: { activeTooltipIndex?: number | string | null }) => {
    if (s?.activeTooltipIndex != null) onPick(Number(s.activeTooltipIndex));
  };
  const hasModel = data.some((d) => d.model != null);
  const hasBand = data.some((d) => d.band != null);
  const hasPm = hasModel || data.some((d) => d.pm25 != null);
  const xAxis = <XAxis dataKey="time" tickFormatter={(t: string) => (t.slice(11, 13) === "00" ? dayHour(t).slice(0, 5) : hhmm(t))} interval={data.length > 60 ? 7 : 3} tick={axis} tickLine={false} axisLine={{ stroke: "var(--line)" }} />;
  const marks = (
    <>
      {now && <ReferenceLine x={now} stroke="var(--ink-3)" strokeDasharray="3 3" label={{ value: "now", position: "insideTopLeft", fontSize: 10, fill: "var(--ink-3)" }} />}
      {sel && <ReferenceLine x={sel} stroke="var(--accent)" strokeWidth={2} />}
    </>
  );

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <figure>
        <figcaption className="mb-1 text-sm font-semibold">Stagnation score, next {fc.hours.length} h <span className="faint font-normal">(0–100, higher traps more pollution)</span></figcaption>
        <div className="h-[190px]" aria-label="Line chart of the hourly stagnation score">
          <ResponsiveContainer>
            <AreaChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }} onClick={pick}>
              <CartesianGrid vertical={false} {...grid} />
              <ReferenceArea y1={60} y2={100} fill="var(--warn)" fillOpacity={0.1} ifOverflow="hidden" />
              <ReferenceArea y1={35} y2={60} fill="var(--watch)" fillOpacity={0.1} ifOverflow="hidden" />
              {xAxis}
              <YAxis domain={[0, 100]} ticks={[0, 35, 60, 100]} tick={axis} tickLine={false} axisLine={false} />
              <Tooltip content={<Tip unit="" kind="score" />} cursor={{ stroke: "var(--ink-3)" }} />
              {marks}
              <Area type="monotone" dataKey="score" stroke="var(--accent)" strokeWidth={2} fill="var(--accent)" fillOpacity={0.12} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="faint -mt-1 text-[11px]">Shaded bands: 35–60 watch, 60+ inversion warning.</div>
      </figure>
      <figure>
        <figcaption className="mb-1 text-sm font-semibold">PM2.5 forecast, city-wide <span className="faint font-normal">(µg/m³, {hasModel ? "learned model" : "CAMS model ~11 km grid"})</span></figcaption>
        <div className="h-[190px]" aria-label="Line chart of forecast PM2.5">
          {hasPm ? (
            <ResponsiveContainer>
              <ComposedChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }} onClick={pick}>
                <CartesianGrid vertical={false} {...grid} />
                {xAxis}
                <YAxis tick={axis} tickLine={false} axisLine={false} domain={[0, (m: number) => Math.max(40, Math.ceil(m / 10) * 10)]} />
                <Tooltip content={<Tip unit="µg/m³" kind="pm" />} cursor={{ stroke: "var(--ink-3)" }} />
                <ReferenceLine y={PM_WATCH} stroke="var(--ink-3)" strokeDasharray="2 4" label={{ value: `WHO 24 h ${PM_WATCH}`, position: "insideBottomRight", fontSize: 10, fill: "var(--ink-3)" }} />
                <ReferenceLine y={PM_WARNING} stroke="var(--ink-3)" strokeDasharray="4 4" label={{ value: `EU 24 h limit ${PM_WARNING}`, position: "insideTopRight", fontSize: 10, fill: "var(--ink-3)" }} />
                {marks}
                {hasBand && <Area type="monotone" dataKey="band" stroke="none" fill="var(--warn)" fillOpacity={0.14} isAnimationActive={false} activeDot={false} />}
                {hasModel && <Line type="monotone" dataKey="pm25" stroke="var(--ink-3)" strokeOpacity={0.6} strokeWidth={1.25} strokeDasharray="3 3" dot={false} activeDot={false} isAnimationActive={false} />}
                <Line type="monotone" dataKey={hasModel ? "model" : "pm25"} stroke="var(--warn)" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <div className="faint grid h-full place-items-center text-sm">PM2.5 forecast unavailable</div>
          )}
        </div>
        <div className="faint -mt-1 text-[11px]">
          {hasModel ? `Solid: learned estimate for the whole city${hasBand ? ", shaded: 10th to 90th percentile" : ""}. Dashed grey: raw regional model (CAMS, ~11 km), which runs high in episodes.`
            : "Coarse regional value for the whole city; the map dots show local citizen sensors."}
        </div>
      </figure>
    </div>
  );
}
