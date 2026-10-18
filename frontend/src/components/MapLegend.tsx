import { useState } from "react";

const Dot = ({ c, ring = "#fff" }: { c: string; ring?: string }) => <i className="mr-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: c, border: `1.5px solid ${ring}` }} />;
const Line = ({ c, dashed, w = 3 }: { c: string; dashed?: boolean; w?: number }) => (
  <i className="mr-1.5 inline-block w-5 shrink-0 align-middle" style={{ height: 0, borderTop: `${w}px ${dashed ? "dashed" : "solid"} ${c}` }} />
);

export type LegendKind = "air" | "heat" | "clean" | "route";

const KEY: Record<LegendKind, React.ReactNode[]> = {
  air: [
    <><Dot c="#2f9e63" /><Dot c="#e0b030" /><Dot c="#c0392f" /> Citizen sensors, coloured by PM2.5</>,
    <><Dot c="#2f9e63" ring="#10261a" /> Airthings pilot stations (hourly)</>,
    <><i className="mr-1.5 inline-block h-3 w-3 shrink-0 rounded-full" style={{ background: "#184e36", border: "3px solid #96d6ac" }} /> Official air station (name, PM10)</>,
  ],
  heat: [<>Feels-like temperature for the hour: the city forecast plus how much warmer or cooler each place usually is in this weather.</>, <>Built-up areas hold heat on calm nights; higher ground is cooler.</>],
  clean: [
    <><i className="mr-1.5 inline-block h-3 w-5 shrink-0 rounded" style={{ background: "#09422a", opacity: 0.8 }} /> Green haze: where the cleanest routes run</>,
    <><i className="mr-1.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white" style={{ background: "#d6288c" }}>1</i> Busy street with poor air</>,
  ],
  route: [
    <><Dot c="#1f5b3f" /> A start <Dot c="#c0392f" /><span className="ml-1">B end</span></>,
    <><Line c="#4a5563" w={4} /> Fastest <Line c="#e0961e" w={4} /> Balanced <Line c="#1f8a70" w={4} /> Cleanest</>,
    <><Line c="#2563eb" w={4} /> Your setting <Line c="#5a5a5a" dashed w={4} /> Google</>,
    <>Faint squares underneath: air quality</>,
  ],
};

/** Compact legend on the map: the colour bar for the current layer, with "?" opening the full key. */
export default function MapLegend({ kind, title, children }: { kind: LegendKind; title: string; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="legend" aria-label="Map legend">
      <div className="flex items-center justify-between gap-2 font-bold">
        <span>{title}</span>
        <button className="legend-q" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? "Hide full legend" : "Show full legend"}>{open ? "–" : "?"}</button>
      </div>
      {children}
      {open && (
        <ul className="muted mt-2 grid gap-1.5 border-t pt-2" style={{ borderColor: "var(--line)" }}>
          {KEY[kind].map((r, i) => <li key={i} className="flex flex-wrap items-center">{r}</li>)}
        </ul>
      )}
    </div>
  );
}
