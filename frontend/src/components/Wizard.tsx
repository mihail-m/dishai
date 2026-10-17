import { useState } from "react";
import { api } from "../api";

const HEATING = [
  ["wood", "Wood"], ["coal", "Coal"], ["electric", "Electric"], ["gas", "Gas"], ["district_heating", "District heating"], ["other", "Other"],
] as const;

export default function Wizard({ district, grantNote }: { district: string; grantNote: string }) {
  const [heating, setHeating] = useState<string>("wood");
  const [home, setHome] = useState<boolean | null>(null);
  const [aid, setAid] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ eligible: boolean; message: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const yn = (v: boolean | null, set: (b: boolean) => void, name: string) => (
    <div className="seg" role="group" aria-label={name}>
      <button aria-pressed={v === true} onClick={() => set(true)}>Yes</button>
      <button aria-pressed={v === false} onClick={() => set(false)}>No</button>
    </div>
  );

  const submit = async () => {
    setBusy(true); setErr(null);
    const eligible = (heating === "wood" || heating === "coal") && home === true && aid === true;
    try {
      const r = await api.lead(district, heating, eligible);
      setDone({ eligible, message: r.message });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="mt-3 rounded-xl p-3 text-sm" style={{ background: done.eligible ? "var(--good-bg)" : "var(--surface-2)" }} role="status">
        <div className="font-semibold">{done.eligible ? "You are likely eligible for C.3.2 support." : "You may not meet every criterion, but your interest is counted."}</div>
        <p className="muted mt-1">{grantNote}</p>
        <p className="faint mt-1 text-xs">Recorded for {district}: heating type and district only, as an anonymous count. No name, address or contact details were stored.</p>
      </div>
    );
  }
  return (
    <div className="mt-3 grid gap-3 rounded-xl p-3 text-sm" style={{ background: "var(--surface-2)" }}>
      <div className="font-semibold">Quick eligibility check (C.3.2)</div>
      <label className="grid gap-1">
        <span className="muted">1. What do you mainly heat with?</span>
        <select value={heating} onChange={(e) => setHeating(e.target.value)}>
          {HEATING.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <div className="grid gap-1"><span className="muted">2. Do you own a single-family home?</span>{yn(home, setHome, "owns a single-family home")}</div>
      <div className="grid gap-1"><span className="muted">3. Does your household receive heating aid?</span>{yn(aid, setAid, "receives heating aid")}</div>
      <button className="btn btn-primary justify-self-start" disabled={busy || home === null || aid === null} onClick={submit}>
        {busy ? "Sending…" : "Check and register interest"}
      </button>
      {err && <p className="text-xs" style={{ color: "var(--warn)" }}>Could not send: {err}</p>}
      <p className="faint text-xs">Only your district ({district}) and heating type are sent. Nothing personal is stored.</p>
    </div>
  );
}
