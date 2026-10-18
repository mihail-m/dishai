import { useEffect, useState } from "react";
import type { Scenario } from "../types";
import { Acc } from "./ui";

interface Day { date: string; pm10: number; pm25: number; usual_pm10: number | null; usual_pm25: number | null; worse_than_usual: boolean; dust_max: number; dust_share: number | null }
interface Plume { kind: string; label: string; evidence: string[]; vertical: string | null }
interface PlumesResp {
  plume: { days: Day[]; plume: Plume; yardstick: boolean } | null;
  green_ticket: { date: string; source: string; note: string }[];
  sources: { cams_dust?: string; fires?: { enabled: boolean; detections?: number; error?: string } };
}

const dayDiff = (iso: string, from: string) => Math.round((Date.parse(`${iso}T12:00Z`) - Date.parse(`${from}T12:00Z`)) / 864e5);
const dayName = (iso: string, from: string) => { const n = dayDiff(iso, from); return n === 0 ? "Today" : n === 1 ? "Tomorrow" : new Date(`${iso}T12:00`).toLocaleDateString("en-GB", { weekday: "long" }); };

/** What the Copernicus CAMS forecast says about dust, smoke and particles for the next days, next to what is usual for the month. */
export default function PlumeCard({ scenario }: { scenario: Scenario }) {
  const [d, setD] = useState<PlumesResp | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(`/api/plumes${scenario === "live" ? "" : `?scenario=${scenario}`}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: PlumesResp) => { if (live) { setD(j); setErr(false); } })
      .catch(() => { if (live) setErr(true); });
    return () => { live = false; };
  }, [scenario]);
  if (err) return null;
  const p = d?.plume;
  const ticket = d?.green_ticket ?? [];
  return (
    <Acc title={<>Dust, smoke and regional air{p?.plume.kind && p.plume.kind !== "none" ? <span className="badge ml-2">{p.plume.label}</span> : null}</>}>
      {!d ? <div className="h-16 animate-pulse" /> : !p ? <p className="muted m-0 text-sm">Not available in a demo scenario: the regional forecast is live data.</p> : (
        <div className="grid gap-3">
          <p className="m-0 text-sm"><b>{p.plume.label}.</b> {p.plume.evidence.join(". ")}{p.plume.evidence.length ? "." : " CAMS shows no dust or smoke plume, and no fires that matter for Sofia, in the next 36 hours."}</p>
          {p.plume.vertical && <p className="muted m-0 text-[13px]">{p.plume.vertical}</p>}
          <div role="table" aria-label="Forecast by day" className="grid gap-1 text-[13px]">
            <div role="row" className="faint grid grid-cols-[1fr_auto_auto_auto] gap-x-3 text-[11px] font-bold uppercase tracking-wide"><span role="columnheader">Day</span><span role="columnheader">PM2.5</span><span role="columnheader">PM10</span><span role="columnheader">Dust peak</span></div>
            {p.days.map((x) => (
              <div role="row" key={x.date} className="grid grid-cols-[1fr_auto_auto_auto] items-baseline gap-x-3">
                <span role="cell" className="font-semibold">{dayName(x.date, p.days[0].date)}{x.worse_than_usual && <span className="badge ml-1.5">worse than usual</span>}</span>
                <span role="cell">{Math.round(x.pm25)}{x.usual_pm25 != null && <span className="faint"> / {Math.round(x.usual_pm25)}</span>}</span>
                <span role="cell">{Math.round(x.pm10)}{x.usual_pm10 != null && <span className="faint"> / {Math.round(x.usual_pm10)}</span>}</span>
                <span role="cell">{Math.round(x.dust_max)}</span>
              </div>
            ))}
          </div>
          <p className="faint m-0 text-[11px]">µg/m³, daily mean. Grey: what a usual day of this month looks like in the same forecast. Dust peak is the highest hour.</p>
          <p className="m-0 text-sm">
            {ticket.length
              ? <><b>Green ticket:</b> announced for {ticket.map((t) => dayName(t.date, p.days[0]?.date ?? t.date)).join(", ")}.</>
              : <><b>Green ticket:</b> none announced. Sofia Municipality decides case by case, usually a day ahead, and publishes it on its website and in the press.</>}
          </p>
          <p className="faint m-0 text-[11px]">Source: Copernicus Atmosphere Monitoring Service (CAMS) Europe forecast via Open-Meteo{d.sources.fires?.enabled && d.sources.fires.detections != null ? `; ${d.sources.fires.detections} active-fire detections in the last 24 h from NASA FIRMS` : ""}.</p>
        </div>
      )}
    </Acc>
  );
}
