import type { Forecast } from "../types";
import { dayHour } from "../api";

export default function HourSlider({ fc, hourIndex, setHourIndex, extra }: { fc: Forecast | null; hourIndex: number; setHourIndex: (i: number) => void; extra?: React.ReactNode }) {
  const h = fc?.hours[hourIndex];
  return (
    <div className="card px-4 py-3">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <label htmlFor="hour" className="text-sm font-semibold">Forecast hour: <span className="font-mono">{h ? dayHour(h.time) : "…"}</span></label>
        <div className="flex items-center gap-3">
          {extra}
          {fc && <button className="btn !py-1 text-xs" onClick={() => setHourIndex(fc.now_index)}>Now</button>}
        </div>
      </div>
      <input id="hour" type="range" min={0} max={(fc?.hours.length ?? 48) - 1} value={hourIndex} onChange={(e) => setHourIndex(Number(e.target.value))} />
    </div>
  );
}
