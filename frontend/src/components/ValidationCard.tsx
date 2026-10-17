import { useEffect, useState } from "react";
import type { Scenario, Validation, ValidationBlock } from "../types";
import { api2 } from "../api";

const fmt = (v: number | null | undefined) => (v == null ? "n/a" : v.toFixed(2));

function Block({ title, b }: { title: string; b: ValidationBlock }) {
  const worse = b.median_abs_error > b.baselines.constant_median.median_abs_error;
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold">{title}</span>
        <span className="badge" style={{ background: b.verdict === "weak" ? "var(--warn-bg)" : "var(--watch-bg)", color: b.verdict === "weak" ? "var(--warn)" : "var(--watch)" }}>{b.verdict}</span>
      </div>
      <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 text-xs">
        <dt className="muted">Spearman rank correlation</dt><dd className="tabular-nums font-semibold">{fmt(b.spearman)}</dd>
        <dt className="muted">Median error, model</dt><dd className="tabular-nums">{fmt(b.median_abs_error)} µg/m³</dd>
        <dt className="muted">Median error, constant median</dt><dd className="tabular-nums">{fmt(b.baselines.constant_median.median_abs_error)} µg/m³</dd>
      </dl>
      <p className="faint mt-1 text-[11px]">{b.n} sensors held out one at a time.{worse ? " The model is not better than guessing the median level." : ""}</p>
    </div>
  );
}

export default function ValidationCard({ scenario }: { scenario: Scenario }) {
  const [v, setV] = useState<Validation | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let off = false;
    setV(null); setErr(false);
    api2.validation(scenario).then((x) => !off && setV(x)).catch(() => !off && setErr(true));
    return () => { off = true; };
  }, [scenario]);
  return (
    <div className="card p-4">
      <div className="text-sm font-semibold">How far to trust this map</div>
      <p className="faint mt-0.5 text-xs">Leave-one-out check against outdoor citizen sensors.</p>
      {err && <p className="muted mt-2 text-xs">Validation unavailable.</p>}
      {!v && !err && <div className="mt-2 h-20 animate-pulse rounded-lg" style={{ background: "var(--surface-2)" }} />}
      {v?.warning && <p className="mt-2 text-xs" style={{ color: "var(--watch)" }}>{v.warning}</p>}
      {v && !v.warning && (
        <div className="mt-2 grid gap-2">
          <Block title="Latest reading" b={v} />
          {v.daily_mean && <Block title="24 h mean" b={v.daily_mean} />}
          <p className="muted text-xs">
            {v.verdict === "weak" ? (
              <>With sensors reading about {v.observed.median} µg/m³, the street-level pattern is <b>not confirmed</b> by the data: sensor noise is as large as the signal. Treat the map as an indicative screening, not a measurement. A polluted winter day would be a fairer test.</>
            ) : (
              <>The street-level pattern is partly supported by the sensors. Still an indicative screening, not a measurement.</>
            )}
          </p>
        </div>
      )}
    </div>
  );
}
