import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Dashboard as D, NetworkResp, Scenario } from "../types";
import { PM_LEVEL_TEXT, api, api2 } from "../api";
import { caiCss } from "../map/cai";
import { MEASURE_COLOR } from "../views/NetworkView";

export default function Dashboard({ scenario, active }: { scenario: Scenario; active: boolean }) {
  const [d, setD] = useState<D | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [net, setNet] = useState<NetworkResp | null>(null);

  useEffect(() => {
    if (!active) return;
    let off = false;
    api2.network(scenario).then((n) => !off && setNet(n)).catch(() => {});
    return () => { off = true; };
  }, [scenario, active]);

  useEffect(() => {
    if (!active) return;
    let off = false;
    const load = () => api.dashboard(scenario).then((x) => !off && setD(x)).catch((e) => !off && setErr(String(e)));
    load();
    const t = setInterval(load, 8000);
    return () => { off = true; clearInterval(t); };
  }, [scenario, active]);

  if (err) return <div className="card p-4 text-sm" style={{ color: "var(--warn)" }}>Could not load dashboard: {err}</div>;
  if (!d) return <div className="card h-64 animate-pulse" />;

  const bars = d.leads_by_district.map((r) => ({ district: r.district, label: `${r.district}  ${r.leads}`, sample: r.sample_leads, submitted: r.submitted_leads, total: r.leads }));
  const top = d.hotspots[0];

  return (
    <div className="mx-auto grid max-w-[1040px] gap-4">
      <div>
        <h2 className="m-0 text-[1.75rem] font-bold">City insights</h2>
        <p className="muted m-0 mt-1">Where the air is worst, who is affected and which measures from the Sofia climate plan help. For the municipality and planners.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Top hotspot (next 24 h)" value={top?.district ?? "–"} sub={top ? `peak ${Math.round(top.peak_24h)} × ${top.leads} leads` : ""} />
        <Tile label="C.3.2 leads" value={String(d.leads_total)} sub={`${d.leads_total - d.leads_submitted} sample + ${d.leads_submitted} submitted live`} />
        <Tile label="Active sensors" value={String(d.sensors_active)} sub={d.sensors_simulated ? "readings simulated for the demo" : "citizen sensors, outdoor"} />
        <Tile label="Warning window" value={d.alert.active ? `${d.alert.start_hhmm}–${d.alert.end_hhmm}` : "none"} sub={d.alert.message} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card p-4">
          <h2 className="font-semibold">Hotspot districts <span className="faint text-xs font-normal">peak score next 24 h × leads (A.3.4, C.3.2)</span></h2>
          <ol className="mt-3 grid gap-2">
            {d.hotspots.map((h, i) => (
              <li key={h.district} className="flex items-center justify-between gap-3 rounded-lg px-3 py-2" style={{ background: "var(--surface-2)" }}>
                <span className="flex items-center gap-3">
                  <span className="faint w-4 text-sm tabular-nums">{i + 1}</span>
                  <span className="font-semibold">{h.district}</span>
                  <span className={`badge pill-${h.level}`}>{PM_LEVEL_TEXT[h.level]}</span>
                </span>
                <span className="muted text-sm tabular-nums">{Math.round(h.peak_24h)} × {h.leads} = <b className="text-[var(--ink)]">{Math.round(h.hotspot_index)}</b></span>
              </li>
            ))}
          </ol>
        </section>

        <section className="card p-4">
          <h2 className="font-semibold">Home-renovation leads by district <span className="faint text-xs font-normal">(C.3.2)</span></h2>
          <div className="mt-1 flex gap-4 text-xs">
            <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--ink-3)" }} />Sample data</span>
            <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--accent)" }} />Submitted in this demo</span>
          </div>
          <div className="mt-2 h-[300px]" aria-label="Stacked bar chart of leads by district">
            <ResponsiveContainer>
              <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 28, left: 8, bottom: 0 }} barCategoryGap={6}>
                <CartesianGrid horizontal={false} stroke="var(--line)" strokeDasharray="2 4" />
                <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} />
                <YAxis type="category" dataKey="label" width={104} tick={{ fontSize: 12, fill: "var(--ink-2)" }} tickLine={false} axisLine={false} interval={0} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12 }} />
                <Bar dataKey="sample" name="Sample" stackId="a" fill="var(--ink-3)" stroke="var(--surface)" strokeWidth={2} />
                <Bar dataKey="submitted" name="Submitted" stackId="a" fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} radius={[0, 4, 4, 0]}>
                  </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      <section className="card p-4">
        <h2 className="font-semibold">Busy streets with poor air <span className="faint text-xs font-normal">where many routes are forced through dirty air · ranked by usage × (1 − score/100)</span></h2>
        {!net && <div className="mt-3 h-24 animate-pulse rounded-lg" style={{ background: "var(--surface-2)" }} />}
        {net && (
          <ol className="mt-3 grid gap-2 lg:grid-cols-2">
            {net.chokepoints.slice(0, 8).map((c) => (
              <li key={c.rank} className="rounded-lg px-3 py-2" style={{ background: "var(--surface-2)" }}>
                <div className="flex items-center gap-2 text-sm">
                  <span className="faint w-4 tabular-nums">{c.rank}</span>
                  <span className="font-semibold">{c.street}</span>
                  <span className="faint text-xs">{c.district}</span>
                  <span className="ml-auto flex items-center gap-1 text-xs tabular-nums"><i className="inline-block h-2 w-2 rounded-full" style={{ background: caiCss(c.mean_cai) }} />{Math.round(c.mean_cai)}</span>
                  <span className="muted text-xs tabular-nums">score {c.score}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {c.measures.map((m) => (
                    <span key={m.id} title={m.why} className="badge" style={{ background: MEASURE_COLOR[m.id], color: "#fff", borderColor: "transparent" }}>{m.id} {m.label}</span>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        )}
        <p className="faint mt-3 text-xs">Indicative screening: from {net?.n_od ?? 400} seeded random walks on the modelled air quality score. Measures are suggestions mapped to the plan, not engineering assessments.</p>
      </section>

      <section className="card p-4">
        <h2 className="font-semibold">Wash these streets first <span className="faint text-xs font-normal">(A.4.7) · <b>sample street names</b></span></h2>
        <ol className="mt-3 grid gap-2 sm:grid-cols-2">
          {d.street_washing.map((s) => (
            <li key={s.rank} className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm" style={{ background: "var(--surface-2)" }}>
              <span className="faint w-5 tabular-nums">{s.rank}</span>
              <span className="font-medium">{s.street}</span>
              <span className="muted ml-auto">{s.district}</span>
              <span className="badge">sample</span>
            </li>
          ))}
        </ol>
        <p className="faint mt-3 text-xs">{d.street_washing_note}</p>
      </section>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card p-4">
      <div className="eyebrow">{label}</div>
      <div className="display mt-1 text-2xl font-bold tabular-nums">{value}</div>
      <div className="muted mt-0.5 text-xs">{sub}</div>
    </div>
  );
}
