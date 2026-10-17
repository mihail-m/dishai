import type { Forecast } from "../types";
import { dayHour } from "../api";
import type { PreloadStatus } from "../forecast";

export default function HourSlider({ fc, hourIndex, setHourIndex, ready, refreshing, onRefresh, progress, error }: {
  fc: Forecast | null; hourIndex: number; setHourIndex: (i: number) => void; ready: boolean; refreshing: boolean;
  onRefresh: () => void; progress: PreloadStatus | null; error: string | null;
}) {
  const h = fc?.hours[hourIndex];
  return (
    <div className="card px-4 py-3">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <label htmlFor="hour" className="text-sm font-semibold">Forecast hour: <span className="font-mono">{h ? dayHour(h.time) : "…"}</span></label>
        <div className="flex items-center gap-3">
          <button className="btn !py-1 text-xs" onClick={onRefresh} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh forecast"}</button>
          {fc && <button className="btn !py-1 text-xs" onClick={() => setHourIndex(fc.now_index)} disabled={!ready}>Now</button>}
        </div>
      </div>
      <input id="hour" type="range" min={0} max={23} value={hourIndex} disabled={!ready} onChange={(e) => setHourIndex(Number(e.target.value))} />
      <div className="faint mt-1 flex flex-wrap justify-between gap-2 text-[11px]" role="status" aria-live="polite">
        <span>{ready ? "24 hours ready · instant playback" : `Preparing 24 hours${progress ? ` · ${progress.loaded}/${progress.total}` : "…"}`}</span>
        {fc && <span>{dayHour(fc.hours[0].time)} – {dayHour(fc.hours[23].time)} · updated {dayHour(fc.generated_at)}</span>}
      </div>
      {(error || progress?.error) && <p role="alert" className="mt-2 text-xs" style={{ color: "var(--warn)" }}>
        {error ? <>Could not update the forecast: {error}. {fc ? "The last loaded forecast is kept. " : ""}</>
          : <>Could not prepare all 24 hours for this view: {progress?.error}. </>}
        Use Refresh forecast to retry.
      </p>}
    </div>
  );
}
