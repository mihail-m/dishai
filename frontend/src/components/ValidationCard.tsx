import { useEffect, useState } from "react";
import type { MlValidation, Scenario, Trust, Validation, ValidationBlock } from "../types";
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

function MlBlock({ m }: { m: MlValidation }) {
  const o = m.offline_eval, e = m.episodes;
  if (!o) return null;
  const sign = (x: number) => `${x > 0 ? "+" : ""}${x.toFixed(1)}`;
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold">City level (learned model)</span>
        <span className="faint text-[11px]">trained {m.trained}{m.sensors ? ` · ${m.sensors} sensors` : ""}</span>
      </div>
      <p className="faint mt-0.5 text-[11px]">Tested on sensors and days it never saw (Sofia, {m.span?.[0].slice(0, 7)} to {m.span?.[1].slice(0, 7)}).</p>
      <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 text-xs">
        <dt className="muted">Average error, learned model</dt><dd className="tabular-nums font-semibold">{o.ml.mae.toFixed(1)} µg/m³</dd>
        <dt className="muted">Average error, regional model × sensor bias</dt><dd className="tabular-nums">{o.cams_x_bias.mae.toFixed(1)} µg/m³</dd>
        <dt className="muted">Average error, &ldquo;city median stays&rdquo;</dt><dd className="tabular-nums">{o.persistence_city_median.mae.toFixed(1)} µg/m³</dd>
        {e && <>
          <dt className="muted">Forecast episodes (CAMS ≥ 50): learned</dt><dd className="tabular-nums font-semibold">{e.ml.mae.toFixed(1)} ({sign(e.ml.bias)} bias)</dd>
          <dt className="muted">Forecast episodes: regional model × bias</dt><dd className="tabular-nums">{e.cams_x_bias.mae.toFixed(1)} ({sign(e.cams_x_bias.bias)} bias)</dd>
        </>}
        {m.median_abs_error != null && <><dt className="muted">Today&apos;s check, {m.n} sensors held out</dt><dd className="tabular-nums">{fmt(m.median_abs_error)} µg/m³</dd></>}
      </dl>
    </div>
  );
}

function TrustBlocks({ t }: { t: Trust }) {
  const c = t.calibration, s = t.spatial, a = t.alert;
  const ph = s?.polluted_heating;
  const rh = c ? Object.values(c.ratio_by_rh) : [];
  const alertRed = a && Object.entries(a.test_period).find(([k]) => k.startsWith("model P >= p*"));
  const alertOld = a && Object.entries(a.test_period).find(([k]) => k.startsWith("stagnation"));
  return (
    <>
      {c && (
        <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}>
          <div className="text-xs font-semibold">Sensors vs a reference station</div>
          <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 text-xs">
            <dt className="muted">Raw sensor median vs {c.reference}</dt><dd className="tabular-nums">{c.raw_mae?.toFixed(1)} µg/m³ off</dd>
            <dt className="muted">After calibration (weeks it never saw)</dt><dd className="tabular-nums font-semibold">{c.calibrated_mae?.toFixed(1)} µg/m³ off{c.mae_reduction_pct != null ? ` (−${c.mae_reduction_pct}%)` : ""}</dd>
            {rh.length > 0 && <><dt className="muted">Calibrated ÷ reference, dry to humid air</dt><dd className="tabular-nums">{Math.min(...rh).toFixed(2)}–{Math.max(...rh).toFixed(2)}</dd></>}
            <dt className="muted">Typical error of the calibrated city value</dt><dd className="tabular-nums">about ±{c.typical_error_pct}%</dd>
          </dl>
          <p className="faint mt-1 text-[11px]">Raw citizen sensors read about half of the reference. Only one reference station in Sofia has usable PM2.5, so heavy episodes (above 50 µg/m³) are checked on {" "}a handful of hours only.</p>
        </div>
      )}
      {s && (
        <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}>
          <div className="text-xs font-semibold">Street by street</div>
          <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 text-xs">
            {ph && <>
              <dt className="muted">Ranking of sensors within an hour, polluted winter hours</dt>
              <dd className="tabular-nums font-semibold">{fmt(ph.krige_offsets_removed.within_hour_spearman)} <span className="faint font-normal">(old method {fmt(ph.idw_current.within_hour_spearman)})</span></dd>
              <dt className="muted">Median error at a hidden sensor</dt>
              <dd className="tabular-nums">{fmt(ph.krige_offsets_removed.median_abs_error)} µg/m³ <span className="faint">(old {fmt(ph.idw_current.median_abs_error)})</span></dd>
              <dt className="muted">80 % ranges hold</dt><dd className="tabular-nums">{Math.round(ph.coverage80 * 100)}% of readings</dd>
            </>}
            <dt className="muted">Sensors differ persistently from each other by</dt><dd className="tabular-nums">±{s.offset_sd_pct}%</dd>
            {s.range_km != null && <><dt className="muted">Pollution pattern varies over about</dt><dd className="tabular-nums">{s.range_km} km</dd></>}
          </dl>
          <p className="faint mt-1 text-[11px]">What the sensors support is how a neighbourhood differs from the city; they say little about one street versus the next. Street colours are indicative.</p>
        </div>
      )}
      {alertRed && (
        <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}>
          <div className="text-xs font-semibold">The warning banner, back-tested</div>
          <p className="faint mt-0.5 text-[11px]">Winter 2025/26, windows of 24 h, an episode = 3 hours in a row at 25 µg/m³ or more.</p>
          <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 text-xs">
            <dt className="muted">Red banner: episodes caught · false alarms</dt><dd className="tabular-nums font-semibold">{Math.round(alertRed[1].pod * 100)}% · {Math.round(alertRed[1].far * 100)}% of alarms</dd>
            {alertOld && <><dt className="muted">Old rule (trapped air alone): caught · false alarms</dt><dd className="tabular-nums">{Math.round(alertOld[1].pod * 100)}% · {Math.round(alertOld[1].far * 100)}% of alarms</dd></>}
          </dl>
        </div>
      )}
    </>
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
    <div className="grid gap-1">
      <p className="faint mt-0.5 text-xs">Leave-one-out check against outdoor citizen sensors.</p>
      {err && <p className="muted mt-2 text-xs">Validation unavailable.</p>}
      {!v && !err && <div className="mt-2 h-20 animate-pulse rounded-lg" style={{ background: "var(--surface-2)" }} />}
      {v?.warning && <p className="mt-2 text-xs" style={{ color: "var(--watch)" }}>{v.warning}</p>}
      {v && !v.warning && (
        <div className="mt-2 grid gap-2">
          {v.ml && <MlBlock m={v.ml} />}
          {v.trust && <TrustBlocks t={v.trust} />}
          <Block title={v.ml ? "Today's snapshot (street level)" : "Latest reading"} b={v} />
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
