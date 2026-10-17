import { useEffect, useMemo, useState } from "react";
import type { Chokepoint, NetworkResp, Scenario } from "../types";
import type { Stage } from "../stage";
import { api2 } from "../api";
import { caiCss } from "../map/cai";
import { flowLayer, networkStatic } from "../map/layers";

interface Props { stage: Stage; scenario: Scenario; hourIndex: number }

export const MEASURE_COLOR: Record<string, string> = { "A.4.3": "#2a7ab5", "A.4.7": "#4a6a8a", "C.1.1": "#6a3fb5", "A.4.4": "#1f7a5c" };

export default function NetworkView({ stage, scenario, hourIndex }: Props) {
  const [net, setNet] = useState<NetworkResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<Chokepoint | null>(null);
  const [flow, setFlow] = useState(true);
  const [t, setT] = useState(0);
  const { set } = stage;

  useEffect(() => {
    let off = false;
    setNet(null); setErr(null); setSel(null);
    const timer = setTimeout(() => {
      api2.network(scenario, hourIndex).then((n) => !off && setNet(n)).catch((e: Error) => !off && setErr(e.message));
    }, 250);
    return () => { off = true; clearTimeout(timer); };
  }, [scenario, hourIndex]);

  useEffect(() => {
    if (!flow || !net) return;
    let raf = 0, last = 0;
    const tick = (now: number) => {
      if (now - last > 40) { last = now; setT(now / 6000); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [flow, net]);

  const base = useMemo(() => (net ? networkStatic(net, stage.relative, sel) : []), [net, stage.relative, sel]);
  const layers = useMemo(() => (net && flow ? [...base, flowLayer(net, t)] : base), [base, net, flow, t]);

  useEffect(() => {
    set({
      layers, base: "dim", cursor: undefined,
      onPick: (info) => {
        if (info.layer?.id === "net-choke-dot" && info.object) setSel(info.object as Chokepoint);
      },
    });
  }, [layers, set]);
  useEffect(() => () => set({ layers: [], base: "off" }), [set]);

  const choose = (c: Chokepoint) => { setSel(c); stage.map.current?.flyTo(c.lon, c.lat, 15); };

  return (
    <div className="grid gap-3">
      <div className="card p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-semibold">Clean-air network <span className="faint font-normal">· indicative</span></div>
          <label className="muted flex cursor-pointer items-center gap-1.5 text-xs"><input type="checkbox" checked={flow} onChange={(e) => setFlow(e.target.checked)} /> Animate route flow</label>
        </div>
        <p className="muted mt-1 text-xs leading-snug">
          Width = how often streets are used by the <b>cleanest</b> routes between 400 random walks; colour = CAI (same scale as the map). Numbered pink streets are <b>chokepoints</b>: many fastest routes, low CAI.
        </p>
        {net && (
          <dl className="mt-2 grid grid-cols-3 gap-2 text-center">
            {[
              [`${Math.round(net.share_cleaner * 100)}%`, "of walks have a cleaner option"],
              [`${net.when_cleaner.median_dose_change_pct}%`, "exposure, where one exists"],
              [`+${net.when_cleaner.median_time_change_pct}%`, "time, where one exists"],
            ].map(([v, l]) => (
              <div key={l} className="rounded-lg p-2" style={{ background: "var(--surface-2)" }}>
                <dd className="text-lg font-semibold tabular-nums">{v}</dd><dt className="faint text-[10px] leading-tight">{l}</dt>
              </div>
            ))}
          </dl>
        )}
      </div>
      {err && <div className="card p-3 text-sm" style={{ color: "var(--warn)" }}>{err}</div>}
      {!net && !err && <div className="card h-60 animate-pulse" />}
      {net && (
        <div className="card p-3">
          <div className="mb-2 text-sm font-semibold">Chokepoints <span className="faint text-xs font-normal">score = fastest-route use × (1 − CAI/100)</span></div>
          <ol className="grid gap-1.5">
            {net.chokepoints.map((c) => (
              <li key={c.rank}>
                <button className="w-full rounded-lg p-2 text-left" style={{ background: sel?.rank === c.rank ? "var(--surface-2)" : "transparent", border: "1px solid var(--line)" }} onClick={() => choose(c)}>
                  <div className="flex items-center gap-2">
                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white" style={{ background: "#d6288c" }}>{c.rank}</span>
                    <span className="truncate text-sm font-semibold">{c.street}</span>
                    <span className="ml-auto flex items-center gap-1 text-xs tabular-nums"><i className="inline-block h-2 w-2 rounded-full" style={{ background: caiCss(c.mean_cai) }} />CAI {Math.round(c.mean_cai)}</span>
                  </div>
                  <div className="faint mt-0.5 text-[11px]">{c.street_bg} · {c.district} · {c.road_class}</div>
                  <div className="faint text-[11px]">{c.length_m} m of segments · up to {c.max_fast_usage} of 400 fastest routes</div>
                  {sel?.rank === c.rank && (
                    <ul className="mt-1.5 grid gap-1">
                      {c.measures.map((m) => (
                        <li key={m.id} className="text-[11px] leading-snug">
                          <span className="badge mr-1.5" style={{ background: MEASURE_COLOR[m.id], color: "#fff", borderColor: "transparent" }}>{m.id} {m.label}</span>
                          <span className="muted">{m.why}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="faint text-[11px]">{net?.note ?? "400 random walks of 1 to 4.5 km, seeded."} Measures are suggestions mapped to the Sofia climate plan (PDUEK), not engineering assessments.</p>
    </div>
  );
}
